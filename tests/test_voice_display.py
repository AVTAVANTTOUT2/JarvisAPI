"""Contrat backend du Voice HUD : vérité, reprise et flux descendant."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import ValidationError
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config
from api import router_voice_display
from api.ws_handsfree import cancel_current_voice_turn
from jarvis.voice_display import (
    ClaimEvidence,
    SourceEvidence,
    VoiceDisplayEvent,
    answer_from_result,
    voice_display,
)


@pytest.fixture(autouse=True)
def enabled_voice_display(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "VOICE_DISPLAY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "VOICE_DISPLAY_EVENT_RETENTION", 64, raising=False)
    monkeypatch.setattr(
        config, "VOICE_DISPLAY_PRIVACY_TIMEOUT_SECONDS", 300, raising=False
    )
    voice_display.reset()
    yield
    for subscription in tuple(voice_display._subscriptions):
        voice_display.unsubscribe(subscription)
    voice_display.reset()


def test_schema_version_and_confirmed_claim_require_real_evidence():
    with pytest.raises(ValidationError):
        VoiceDisplayEvent(
            schema_version=2,
            sequence=1,
            event_id="evt-1",
            session_id="voice-1",
            type="voice.listening.started",
        )
    with pytest.raises(ValidationError):
        ClaimEvidence(id="claim-1", text="Certain", certainty="confirmed")


def test_partial_final_snapshot_and_monotonic_replay():
    voice_display.ensure_turn(7)
    voice_display.transcript("trente-deux pou", partial=True)
    voice_display.transcript("trente-deux pouces")
    snapshot = voice_display.snapshot()

    assert snapshot.session.transcript_partial == ""
    assert snapshot.session.transcript_final == "trente-deux pouces"
    assert snapshot.session.state == "understanding"
    sequences = [event.sequence for event in voice_display.replay(0)]
    assert sequences == sorted(set(sequences))
    assert [event.sequence for event in voice_display.replay(sequences[-2])] == [
        sequences[-1]
    ]


def test_answer_uses_only_tool_references_and_marks_missing_sources_honestly():
    result = {
        "knowledge": {
            "verified_sources": ["email"],
            "references": [{"source_type": "email", "source_id": "mail-42"}],
        },
        "action": {"type": "mail_read"},
        "action_result": {
            "data": [{
                "source_type": "email",
                "source_id": "mail-42",
                "title": "Compte rendu",
                "excerpt": "La réunion commence à 9 h.",
            }],
        },
    }
    answer = answer_from_result(result, "La réunion commence à 9 h.")

    assert [(source.id, source.status) for source in answer.sources] == [
        ("mail-42", "verified")
    ]
    assert answer.claims[0].source_ids == ["mail-42"]
    assert answer.claims[0].certainty == "confirmed"
    assert answer_from_result({}, "Réponse générale").sources == []


def test_sensitive_values_are_redacted_and_absolute_paths_are_reduced():
    answer = answer_from_result(
        {
            "action_result": {
                "data": [{
                    "source_type": "file",
                    "source_id": "file-1",
                    "title": "token=secret-value-123456",
                    "excerpt": "Bearer abcdefghijklmnop",
                    "locator": "/Users/alice/Documents/private/report.pdf",
                }]
            }
        },
        "api_key=supersecretvalue",
    )
    source = answer.sources[0]
    assert source.title == "[masqué]"
    assert source.excerpt == "[masqué]"
    assert source.locator == "report.pdf"
    assert answer.visual_summary == "[masqué]"
    assert "secret-value" not in str(answer.model_dump())
    assert "/Users/alice" not in str(answer.model_dump())


def test_privacy_navigation_focus_back_and_clear_are_backend_canonical():
    voice_display.ensure_turn(8)
    voice_display.result(
        {
            "knowledge": {
                "verified_sources": ["web"],
                "references": [
                    {"source_type": "web", "source_id": "s1", "reference": "Source 1"},
                    {"source_type": "web", "source_id": "s2", "reference": "Source 2"},
                ],
            },
            "action_result": {
                "data": [
                    {"source_type": "web", "source_id": "s1", "title": "Premier"},
                    {"source_type": "web", "source_id": "s2", "title": "Deuxième"},
                ]
            },
        },
        "Deux résultats.",
    )

    assert voice_display.navigation_command("Ouvre la source 2")["intent"] == "source.open"
    assert voice_display.snapshot().session.current_focus["source_id"] == "s2"
    voice_display.navigation_command("Reviens aux résultats")
    assert voice_display.snapshot().session.current_focus is None
    voice_display.navigation_command("Suivant")
    assert voice_display.snapshot().session.current_focus["item_id"] == "result-1"
    voice_display.navigation_command("Masque l'écran")
    assert voice_display.snapshot().session.privacy_mode is True
    voice_display.navigation_command("Efface l'écran")
    snapshot = voice_display.snapshot().session
    assert snapshot.answer is None
    assert snapshot.privacy_mode is True


def test_sources_can_represent_rejected_failed_and_conflicting_states():
    rejected = SourceEvidence(
        id="s1", title="Source rejetée", status="rejected", error="hors sujet"
    )
    failed = SourceEvidence(
        id="s2", title="Source indisponible", status="unavailable", error="timeout"
    )
    conflict = SourceEvidence(id="s3", title="Source divergente", status="conflicting")
    assert {rejected.status, failed.status, conflict.status} == {
        "rejected", "unavailable", "conflicting"
    }


def test_audio_daemon_ingestion_keeps_response_and_tts_in_sync():
    voice_display.ingest_audio_daemon_event(
        {"state": "processing", "transcript": "Quel est mon prochain rendez-vous ?"}
    )
    voice_display.ingest_audio_daemon_event(
        {"state": "processing", "response": "Votre rendez-vous est à 14 h."}
    )
    voice_display.ingest_audio_daemon_event({"state": "speaking"})
    speaking = voice_display.snapshot().session
    assert speaking.answer.spoken_summary == "Votre rendez-vous est à 14 h."
    assert speaking.state == "speaking"
    assert speaking.active_speech_segment_id == "speech-1"
    voice_display.ingest_audio_daemon_event({"state": "listening"})
    assert voice_display.snapshot().session.active_speech_segment_id is None


def test_disabled_feature_is_a_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "VOICE_DISPLAY_ENABLED", False, raising=False)
    assert voice_display.publish("voice.listening.started") is None
    assert voice_display.replay(0) == []


@pytest.mark.asyncio
async def test_tts_interruption_keeps_the_canonical_text_for_resume():
    voice_display.ensure_turn(12)
    voice_display.result({}, "Réponse à reprendre.")
    voice_display.speech_started()
    session = {
        "turn_id": "turn-12",
        "speech_text": "Réponse à reprendre.",
        "speech_emotion": "neutral",
        "is_speaking": True,
    }

    await cancel_current_voice_turn(session)

    assert session["paused_text"] == "Réponse à reprendre."
    assert session["is_speaking"] is False
    assert voice_display.snapshot().session.state == "result"


def test_bounded_queue_and_soak_replay_do_not_grow_without_limit():
    subscription = voice_display.subscribe()
    for index in range(5_000):
        voice_display.publish("voice.transcript.partial", {"text": f"fragment {index}"})

    assert len(voice_display.replay(0)) == 64
    assert subscription.queue.qsize() == 128
    assert voice_display.snapshot().session.last_sequence == 5_000


def test_hundreds_of_turns_and_reconnections_remain_bounded():
    result = {
        "knowledge": {
            "verified_sources": ["web"],
            "references": [
                {"source_type": "web", "source_id": "s1", "reference": "Source"}
            ],
        },
        "action_result": {
            "data": [{"source_type": "web", "source_id": "s1", "title": "Source"}]
        },
    }

    for turn in range(300):
        subscription = voice_display.subscribe()
        voice_display.ensure_turn(turn)
        voice_display.transcript(f"demande {turn}")
        voice_display.processing("Recherche")
        voice_display.result(result, f"réponse {turn}")
        voice_display.navigation_command("ouvre la source 1")
        voice_display.navigation_command("reviens aux résultats")
        voice_display.unsubscribe(subscription)

    snapshot = voice_display.snapshot().session
    assert len(voice_display.replay(0)) == 64
    assert len(snapshot.navigation_stack) <= 20
    assert voice_display._subscriptions == set()


def _app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    async def activate(_ws):
        return True

    monkeypatch.setattr(router_voice_display, "activate_websocket_profile", activate)
    monkeypatch.setattr(router_voice_display.auth, "is_configured", lambda: True)
    monkeypatch.setattr(
        router_voice_display, "resolve_websocket_auth", lambda _ws: ({"id": "test"}, None)
    )
    app = FastAPI()
    app.include_router(router_voice_display.router)
    return app


def test_snapshot_and_read_only_websocket(monkeypatch: pytest.MonkeyPatch):
    with TestClient(_app(monkeypatch)) as client:
        assert client.get("/api/voice-display/snapshot").status_code == 200
        with client.websocket_connect("/ws/voice-display") as ws:
            first = ws.receive_json()
            assert first["type"] == "voice.display.snapshot"
            voice_display.ensure_turn(9)
            assert ws.receive_json()["type"] == "voice.session.started"
            ws.send_json({"type": "voice.display.cleared"})
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4405


def test_disabled_snapshot_returns_404(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "VOICE_DISPLAY_ENABLED", False, raising=False)
    with TestClient(_app(monkeypatch)) as client:
        assert client.get("/api/voice-display/snapshot").status_code == 404
