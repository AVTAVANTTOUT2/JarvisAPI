"""Contrat du canal WebSocket TV — `/ws/tv/events`.

Le canal est authentifié par le jeton privé du supervisor, limité à la boucle
locale, et strictement descendant. Ces tests verrouillent les trois propriétés
qui le rendent sûr : ce qui entre (rien), ce qui sort (une allowlist), et ce
qui est journalisé (jamais le secret).
"""

from __future__ import annotations

import ast
import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config
from api import ws_tv
from core.supervisor_auth import SUPERVISOR_CONTROL_HEADER
from jarvis import tv_events
from jarvis.event_bus import JarvisEvent, event_bus
from jarvis.tv_events import (
    TV_BUS_EVENT_TRANSLATIONS,
    TV_EVENT_FIELDS,
    TV_EVENT_SCHEMA_VERSION,
    TV_EVENT_TYPES,
    TV_HEARTBEAT,
    TV_NOTIFICATION,
    TV_PAYLOAD_FIELDS,
    TV_SYSTEM,
    TV_TASK,
    TV_VOICE_STATE,
    TvEventError,
    TvEventSubscription,
    build_tv_event,
    relay_bus_event_to_tv,
    translate_bus_event,
    tv_event_hub,
)

PATH = ws_tv.TV_EVENTS_WS_PATH
VALID_TOKEN = "tv-control-secret-" + "z" * 40
OTHER_TOKEN = "tv-control-rotated-" + "w" * 40
PRESENTED_BAD_TOKEN = "tv-control-presented-" + "y" * 40

#: Types du bus qui transportent une conversation. Aucun ne doit être traduit.
CHAT_BUS_EVENT_TYPES = (
    "message.sent",
    "conversation.updated",
    "episode.saved",
    "fact.added",
    "memory.updated",
    "person.upserted",
    "pattern.detected",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def control_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Installe un jeton de contrôle privé isolé du dépôt réel."""
    token_file = tmp_path / ".supervisor_control_token"
    token_file.write_text(VALID_TOKEN, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setattr(
        config, "SUPERVISOR_CONTROL_TOKEN_FILE", str(token_file), raising=False
    )
    return token_file


@pytest.fixture(autouse=True)
def tv_channel_settings(monkeypatch: pytest.MonkeyPatch):
    """Réglages déterministes : pas de heartbeat parasite pendant les assertions."""
    monkeypatch.setattr(config, "TV_EVENTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TV_EVENTS_INCLUDE_TRANSCRIPTS", False, raising=False)
    monkeypatch.setattr(config, "TV_EVENTS_DEVICE_ID", "tv_test", raising=False)
    monkeypatch.setattr(config, "TV_WS_MAX_CONNECTIONS", 4, raising=False)
    monkeypatch.setattr(config, "TV_WS_QUEUE_MAXSIZE", 100, raising=False)
    monkeypatch.setattr(config, "TV_WS_MAX_DROPPED_EVENTS", 200, raising=False)
    monkeypatch.setattr(config, "TV_WS_HEARTBEAT_SECONDS", 30.0, raising=False)
    monkeypatch.setattr(config, "TV_WS_SEND_TIMEOUT_SECONDS", 5.0, raising=False)
    monkeypatch.setattr(config, "TV_WS_MAX_EVENT_BYTES", 8192, raising=False)
    monkeypatch.setattr(config, "TV_WS_MAX_CLIENT_MESSAGE_BYTES", 4096, raising=False)
    monkeypatch.setattr(config, "TV_WS_MAX_CLIENT_VIOLATIONS", 3, raising=False)
    monkeypatch.setattr(config, "TV_EVENT_MAX_TEXT_CHARS", 200, raising=False)
    yield
    # Un test qui échoue au milieu d'une session ne doit pas laisser d'abonné.
    for subscription in tuple(tv_event_hub._subscribers):  # accès interne assumé : nettoyage
        tv_event_hub.unsubscribe(subscription)
    ws_tv._active_connections.clear()  # accès interne assumé : nettoyage


def _asgi_with_peer(app: Any, host: str, port: int = 51234):
    """Enveloppe ASGI qui donne une adresse de pair réaliste au client de test.

    `TestClient` annonce `("testclient", 50000)` : la vérification de boucle
    locale du canal ne serait jamais exercée telle quelle.
    """

    async def wrapped(scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") in {"websocket", "http"}:
            scope = {**scope, "client": (host, port)}
        await app(scope, receive, send)

    return wrapped


def _build_tv_app() -> FastAPI:
    """Application minimale : le canal TV et deux déclencheurs de test."""
    app = FastAPI()
    app.websocket(PATH)(ws_tv.tv_events_websocket)

    @app.post("/_test/publish")
    async def _publish(body: dict) -> dict:
        event = build_tv_event(
            body["type"],
            state=body.get("state", ""),
            payload=body.get("payload"),
            source=body.get("source", "test"),
        )
        return {"delivered": tv_event_hub.publish(event)}

    @app.post("/_test/relay-bus")
    async def _relay_bus(body: dict) -> dict:
        relay_bus_event_to_tv(
            JarvisEvent(type=body["type"], data=body.get("data") or {}, source="test")
        )
        return {"subscribers": tv_event_hub.subscriber_count}

    return app


@pytest.fixture
def tv_client(control_token: Path) -> TestClient:
    """Client de test dont le pair TCP est la boucle locale."""
    return TestClient(_asgi_with_peer(_build_tv_app(), "127.0.0.1"))


@pytest.fixture
def remote_client(control_token: Path) -> TestClient:
    """Client de test vu comme une machine distante."""
    return TestClient(_asgi_with_peer(_build_tv_app(), "203.0.113.7"))


def _auth_headers(token: str = VALID_TOKEN) -> dict[str, str]:
    return {SUPERVISOR_CONTROL_HEADER: token}


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> bool:
    """Attend une condition serveur.

    `TestClient` rend la main dès la fermeture de la socket, sans attendre la
    fin du handler : le nettoyage est constaté, pas supposé.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _expect_close(client: TestClient, headers: dict[str, str] | None = None) -> int:
    """Ouvre le canal, attend la fermeture serveur et retourne son code."""
    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect(PATH, headers=headers or {}) as ws:
            ws.receive_text()
    return refusal.value.code


# ── Authentification ──────────────────────────────────────────────────────────


def test_valid_token_opens_the_channel(tv_client: TestClient):
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            published = client.post(
                "/_test/publish",
                json={"type": TV_NOTIFICATION, "state": "high", "payload": {"title": "Facture"}},
            )
            assert published.json() == {"delivered": 1}
            assert ws.receive_json()["type"] == TV_NOTIFICATION


def test_connection_without_token_is_closed_with_4401(tv_client: TestClient):
    with tv_client as client:
        assert _expect_close(client) == ws_tv.CLOSE_UNAUTHORIZED


def test_invalid_token_is_closed_with_4401(tv_client: TestClient):
    with tv_client as client:
        code = _expect_close(client, _auth_headers(PRESENTED_BAD_TOKEN))
    assert code == ws_tv.CLOSE_UNAUTHORIZED


def test_rotated_token_is_refused_like_an_expired_one(
    tv_client: TestClient,
    control_token: Path,
):
    """Le jeton supervisor n'a pas de TTL : sa rotation vaut expiration."""
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            client.post("/_test/publish", json={"type": TV_HEARTBEAT, "state": "alive"})
            assert ws.receive_json()["type"] == TV_HEARTBEAT

        control_token.write_text(OTHER_TOKEN, encoding="utf-8")
        assert _expect_close(client, _auth_headers()) == ws_tv.CLOSE_UNAUTHORIZED


def test_deleted_token_file_refuses_every_connection(
    tv_client: TestClient,
    control_token: Path,
):
    control_token.unlink()
    with tv_client as client:
        assert _expect_close(client, _auth_headers()) == ws_tv.CLOSE_UNAUTHORIZED


def test_remote_client_is_refused_even_with_a_valid_token(remote_client: TestClient):
    with remote_client as client:
        assert _expect_close(client, _auth_headers()) == ws_tv.CLOSE_UNAUTHORIZED


def test_browser_origin_must_match_exactly(tv_client: TestClient):
    headers = {**_auth_headers(), "Origin": "http://evil.example"}
    with tv_client as client:
        assert _expect_close(client, headers) == ws_tv.CLOSE_FORBIDDEN_ORIGIN


def test_matching_browser_origin_is_accepted(tv_client: TestClient):
    headers = {**_auth_headers(), "Origin": "http://testserver"}
    with tv_client as client:
        with client.websocket_connect(PATH, headers=headers) as ws:
            client.post("/_test/publish", json={"type": TV_HEARTBEAT, "state": "alive"})
            assert ws.receive_json()["type"] == TV_HEARTBEAT


def test_disabled_channel_refuses_connections(
    tv_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TV_EVENTS_ENABLED", False, raising=False)
    with tv_client as client:
        assert _expect_close(client, _auth_headers()) == ws_tv.CLOSE_UNAUTHORIZED


def test_origin_is_checked_before_the_token(tv_client: TestClient):
    """Une page hostile n'obtient aucun signal sur la validité d'un secret."""
    headers = {SUPERVISOR_CONTROL_HEADER: PRESENTED_BAD_TOKEN, "Origin": "http://evil.example"}
    with tv_client as client:
        assert _expect_close(client, headers) == ws_tv.CLOSE_FORBIDDEN_ORIGIN


# ── Journalisation ────────────────────────────────────────────────────────────


def test_refusal_is_logged_as_error(tv_client: TestClient, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG, logger="jarvis.ws_tv")

    with tv_client as client:
        assert _expect_close(client, _auth_headers(PRESENTED_BAD_TOKEN)) == 4401

    refusals = [
        record
        for record in caplog.records
        if record.name == "jarvis.ws_tv" and "connexion refusée" in record.getMessage()
    ]
    assert refusals, "le refus d'authentification doit être journalisé"
    assert all(record.levelno == logging.ERROR for record in refusals)
    assert ws_tv.REFUSAL_TOKEN_INVALID in refusals[0].getMessage()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {SUPERVISOR_CONTROL_HEADER: PRESENTED_BAD_TOKEN},
        {SUPERVISOR_CONTROL_HEADER: VALID_TOKEN, "Origin": "http://evil.example"},
    ],
)
def test_no_token_ever_reaches_the_logs(
    tv_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    headers: dict[str, str],
):
    caplog.set_level(logging.DEBUG)

    with tv_client as client:
        _expect_close(client, headers)

    assert VALID_TOKEN not in caplog.text
    assert PRESENTED_BAD_TOKEN not in caplog.text


# ── Contenu diffusé ───────────────────────────────────────────────────────────


def test_allowed_tv_event_reaches_the_client_with_the_documented_schema(
    tv_client: TestClient,
):
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            client.post(
                "/_test/publish",
                json={
                    "type": TV_TASK,
                    "state": "created",
                    "payload": {"task_id": 7, "title": "Payer le loyer", "priority": "high"},
                },
            )
            event = ws.receive_json()

    assert tuple(event) == TV_EVENT_FIELDS
    assert event["schema_version"] == TV_EVENT_SCHEMA_VERSION
    assert event["type"] == TV_TASK
    assert event["state"] == "created"
    assert event["device_id"] == "tv_test"
    assert isinstance(event["timestamp"], float)
    assert event["payload"] == {"task_id": 7, "title": "Payer le loyer", "priority": "high"}


def test_chat_bus_events_never_reach_the_channel(tv_client: TestClient):
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            for chat_type in CHAT_BUS_EVENT_TYPES:
                client.post(
                    "/_test/relay-bus",
                    json={
                        "type": chat_type,
                        "data": {"content_preview": "secret de famille", "conversation_id": 3},
                    },
                )
            client.post(
                "/_test/relay-bus",
                json={
                    "type": "notification.created",
                    "data": {
                        "notification_id": 1,
                        "source": "email",
                        "priority": "high",
                        "title": "Facture EDF",
                    },
                },
            )
            first = ws.receive_json()

    # Le premier message reçu est la notification : rien de la conversation
    # n'a été mis en file avant elle.
    assert first["type"] == TV_NOTIFICATION
    assert first["payload"]["title"] == "Facture EDF"


def test_transcripts_stay_out_of_the_channel_by_default(tv_client: TestClient):
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            client.post(
                "/_test/publish",
                json={
                    "type": TV_VOICE_STATE,
                    "state": "speaking",
                    "payload": {"jarvis_text": "Bonjour Monsieur", "user_text": "quelle heure"},
                },
            )
            event = ws.receive_json()

    assert event["payload"] == {}


def test_oversized_event_is_not_broadcast(
    tv_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TV_WS_MAX_EVENT_BYTES", 256, raising=False)

    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            oversized = client.post(
                "/_test/publish",
                json={"type": TV_TASK, "state": "created", "payload": {"title": "x" * 200}},
            )
            assert oversized.json() == {"delivered": 0}

            client.post("/_test/publish", json={"type": TV_HEARTBEAT, "state": "alive"})
            assert ws.receive_json()["type"] == TV_HEARTBEAT


# ── Lecture seule ─────────────────────────────────────────────────────────────


def test_repeated_client_writes_close_the_channel(tv_client: TestClient):
    with tv_client as client:
        with pytest.raises(WebSocketDisconnect) as refusal:
            with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
                for _ in range(config.TV_WS_MAX_CLIENT_VIOLATIONS):
                    ws.send_text('{"type":"tv_command","action":"mute"}')
                ws.receive_text()

    assert refusal.value.code == ws_tv.CLOSE_READ_ONLY_VIOLATION


def test_oversized_client_frame_closes_immediately(tv_client: TestClient):
    with tv_client as client:
        with pytest.raises(WebSocketDisconnect) as refusal:
            with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
                ws.send_text("x" * (config.TV_WS_MAX_CLIENT_MESSAGE_BYTES + 1))
                ws.receive_text()

    assert refusal.value.code == ws_tv.CLOSE_PAYLOAD_TOO_LARGE


def test_a_single_write_does_not_kill_a_legitimate_client(tv_client: TestClient):
    """Le budget existe pour tolérer un client bavard, pas pour l'encourager."""
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            ws.send_text("ping")
            client.post("/_test/publish", json={"type": TV_HEARTBEAT, "state": "alive"})
            assert ws.receive_json()["type"] == TV_HEARTBEAT


def test_module_has_no_path_to_command_execution():
    """Le canal TV ne doit jamais pouvoir atteindre le pipeline d'actions."""
    source = (Path(__file__).parents[1] / "api" / "ws_tv.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {"actions", "pipeline", "agents", "api.chat_actions", "api.ws_messages"}
    assert imported & forbidden == set()
    assert "receive_json" not in source
    assert "execute_action" not in source


# ── Cycle de vie, concurrence, backpressure ───────────────────────────────────


def test_disconnect_releases_subscription_and_slot(tv_client: TestClient):
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()):
            assert tv_event_hub.subscriber_count == 1
            assert ws_tv.active_connection_count() == 1

        assert _wait_until(lambda: tv_event_hub.subscriber_count == 0)
        assert _wait_until(lambda: ws_tv.active_connection_count() == 0)


def test_several_clients_receive_the_same_event(tv_client: TestClient):
    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as first:
            with client.websocket_connect(PATH, headers=_auth_headers()) as second:
                published = client.post(
                    "/_test/publish",
                    json={"type": TV_SYSTEM, "state": "service_up", "payload": {"service": "tv"}},
                )
                assert published.json() == {"delivered": 2}
                assert first.receive_json()["state"] == "service_up"
                assert second.receive_json()["state"] == "service_up"


def test_connection_limit_refuses_the_extra_client(
    tv_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TV_WS_MAX_CONNECTIONS", 1, raising=False)

    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()):
            assert _expect_close(client, _auth_headers()) == ws_tv.CLOSE_TOO_MANY_CONNECTIONS


def test_heartbeat_keeps_an_idle_channel_alive(
    tv_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TV_WS_HEARTBEAT_SECONDS", 0.05, raising=False)

    with tv_client as client:
        with client.websocket_connect(PATH, headers=_auth_headers()) as ws:
            event = ws.receive_json()

    assert event["type"] == TV_HEARTBEAT
    assert event["state"] == "alive"


# ── Backpressure et client lent (niveau unitaire) ─────────────────────────────


def test_full_queue_drops_the_oldest_event_and_counts_it():
    subscription = TvEventSubscription(label="test", maxsize=2, max_dropped=10)
    events = [
        build_tv_event(TV_HEARTBEAT, state=f"s{index}", source="test") for index in range(5)
    ]

    for event in events:
        assert subscription.offer(event) is True

    assert subscription.pending == 2
    assert subscription.dropped_events == 3
    assert subscription.is_overflowed is False


def test_subscription_declares_overflow_past_its_budget():
    subscription = TvEventSubscription(label="test", maxsize=1, max_dropped=2)
    for _ in range(5):
        subscription.offer(build_tv_event(TV_HEARTBEAT, source="test"))

    assert subscription.is_overflowed is True


async def test_closing_a_subscription_wakes_a_waiting_reader():
    subscription = TvEventSubscription(label="closing", maxsize=1, max_dropped=2)
    waiting = asyncio.create_task(subscription.next_event(timeout=30.0))
    await asyncio.sleep(0)

    subscription.close()

    assert await asyncio.wait_for(waiting, timeout=0.5) is None
    assert subscription.offer(build_tv_event(TV_HEARTBEAT, source="test")) is False


def test_a_slow_subscriber_never_blocks_the_others():
    slow = tv_event_hub.subscribe(label="slow")
    fast = tv_event_hub.subscribe(label="fast")
    try:
        slow._queue = asyncio.Queue(maxsize=1)  # accès interne assumé : simulation d'un client saturé
        for _ in range(20):
            assert tv_event_hub.publish(build_tv_event(TV_HEARTBEAT, source="test")) == 2
        assert slow.pending == 1
        assert fast.pending == 20
    finally:
        tv_event_hub.unsubscribe(slow)
        tv_event_hub.unsubscribe(fast)


class _StalledWebSocket:
    """WebSocket dont l'envoi n'aboutit jamais — client bloqué mais connecté."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)
        await asyncio.sleep(3600)


async def test_stalled_client_is_disconnected_on_send_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TV_WS_SEND_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(config, "TV_WS_HEARTBEAT_SECONDS", 0.05, raising=False)
    subscription = TvEventSubscription(label="stalled", maxsize=4, max_dropped=10)
    closure = ws_tv._ChannelClosure()  # accès interne assumé : contrat interne testé directement

    await ws_tv._stream_events(_StalledWebSocket(), subscription, closure, "stalled")  # accès interne assumé

    assert closure.code == ws_tv.CLOSE_SLOW_CONSUMER
    assert closure.reason == "envoi_expire"


async def test_overflowed_subscriber_is_disconnected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TV_WS_HEARTBEAT_SECONDS", 0.05, raising=False)
    subscription = TvEventSubscription(label="overflowed", maxsize=1, max_dropped=1)
    for _ in range(5):
        subscription.offer(build_tv_event(TV_HEARTBEAT, source="test"))
    closure = ws_tv._ChannelClosure()  # accès interne assumé

    await ws_tv._stream_events(_StalledWebSocket(), subscription, closure, "overflowed")  # accès interne assumé

    assert closure.code == ws_tv.CLOSE_SLOW_CONSUMER
    assert closure.reason == "client_lent"


# ── Schéma d'événements ───────────────────────────────────────────────────────


def test_unknown_event_type_is_refused_at_construction():
    with pytest.raises(TvEventError, match="tv.unknown"):
        build_tv_event("tv.unknown")


def test_payload_keys_outside_the_allowlist_are_dropped():
    event = build_tv_event(
        TV_NOTIFICATION,
        state="high",
        payload={"title": "Relance", "content": "corps complet du mail", "secret": "s3cr3t"},
    )

    assert set(event.payload) <= TV_PAYLOAD_FIELDS[TV_NOTIFICATION]
    assert "content" not in event.payload
    assert "secret" not in event.payload


def test_secrets_are_redacted_before_broadcast():
    event = build_tv_event(
        TV_SYSTEM,
        state="error",
        payload={"service": "backend", "detail": "échec avec DEEPSEEK_API_KEY=sk-abcdef123456"},
    )

    assert "sk-abcdef123456" not in event.to_json()
    assert "REDACTED" in event.payload["detail"]


def test_long_text_is_truncated_to_the_configured_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TV_EVENT_MAX_TEXT_CHARS", 32, raising=False)

    event = build_tv_event(TV_TASK, state="created", payload={"title": "a" * 500})

    assert len(event.payload["title"]) == 32
    assert event.payload["title"].endswith("…")


def test_state_and_source_are_normalised_to_short_identifiers():
    event = build_tv_event(
        TV_SYSTEM,
        state="  service up !! " + "x" * 80,
        source="database.tasks",
        payload={},
    )

    assert len(event.state) <= 32
    assert " " not in event.state
    assert event.source == "database.tasks"


def test_device_id_falls_back_when_configuration_is_not_an_identifier(
    monkeypatch: pytest.MonkeyPatch,
):
    """`DEVICE_ID=   # commentaire` est lu comme valeur par python-dotenv."""
    monkeypatch.setattr(config, "TV_EVENTS_DEVICE_ID", "", raising=False)
    monkeypatch.setattr(
        config, "DEVICE_ID", "# vide = hostname système (scutil)", raising=False
    )
    tv_events._INVALID_DEVICE_ID_KEYS.clear()  # accès interne assumé : alerte à usage unique

    assert build_tv_event(TV_HEARTBEAT).device_id == "mac_mini"


def test_device_id_uses_the_configured_identifier(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TV_EVENTS_DEVICE_ID", "", raising=False)
    monkeypatch.setattr(config, "DEVICE_ID", "mac-studio.local", raising=False)

    assert build_tv_event(TV_HEARTBEAT).device_id == "mac-studio.local"


def test_transcripts_are_included_only_when_explicitly_allowed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TV_EVENTS_INCLUDE_TRANSCRIPTS", True, raising=False)

    event = build_tv_event(
        TV_VOICE_STATE, state="speaking", payload={"jarvis_text": "Bonjour Monsieur"}
    )

    assert event.payload["jarvis_text"] == "Bonjour Monsieur"


def test_audio_daemon_state_is_translated_into_a_voice_state_event():
    published = tv_events.publish_audio_daemon_state(
        {
            "type": "audio_daemon_state",
            "state": "listening",
            "enabled": True,
            "wake_word_enabled": False,
            "unknown_field": "ignoré",
        }
    )

    assert published is not None
    assert published.type == TV_VOICE_STATE
    assert published.state == "listening"
    assert published.payload == {"enabled": True, "wake_word_enabled": False}


def test_non_daemon_messages_are_not_translated():
    assert tv_events.publish_audio_daemon_state({"type": "response", "content": "salut"}) is None


# ── Contrats structurels ──────────────────────────────────────────────────────


def test_bus_translations_exclude_every_conversation_event():
    assert set(TV_BUS_EVENT_TRANSLATIONS) & set(CHAT_BUS_EVENT_TYPES) == set()
    assert set(TV_BUS_EVENT_TRANSLATIONS.values()) <= TV_EVENT_TYPES


def test_bus_bridge_is_registered_only_for_translated_types():
    handlers = event_bus._handlers  # accès interne assumé : vérification du câblage réel

    for event_type in TV_BUS_EVENT_TRANSLATIONS:
        assert relay_bus_event_to_tv in handlers.get(event_type, [])
    for event_type in CHAT_BUS_EVENT_TYPES:
        assert relay_bus_event_to_tv not in handlers.get(event_type, [])
    assert relay_bus_event_to_tv not in handlers.get("*", [])


def test_conversation_bus_events_have_no_translation():
    for event_type in CHAT_BUS_EVENT_TYPES:
        event = JarvisEvent(type=event_type, data={"content_preview": "privé"}, source="test")
        assert translate_bus_event(event) is None


def test_main_exposes_the_tv_channel_next_to_the_chat_channel():
    import main

    websocket_paths = {
        route.path
        for route in main.app.routes
        if "WebSocket" in type(route).__name__ and getattr(route, "path", None)
    }

    assert PATH in websocket_paths
    assert "/ws" in websocket_paths


def test_tv_server_reuses_the_same_control_header():
    from tv import config as tv_config

    assert tv_config.JARVIS_CONTROL_HEADER == SUPERVISOR_CONTROL_HEADER
    assert tv_config.BACKEND_TV_EVENTS_PATH == PATH


def test_tv_server_connects_only_to_the_tv_channel():
    source = (Path(__file__).parents[1] / "tv" / "server.py").read_text(encoding="utf-8")

    assert "cfg.BACKEND_TV_EVENTS_URL" in source
    # Une seule ouverture de WebSocket, et elle vise le canal TV dédié.
    assert source.count("websockets.connect(") == 1
