"""Restauration fail-closed du snapshot knowledge depuis métadonnées."""

from __future__ import annotations

import pytest

from jarvis.agentic.turn_context import SNAPSHOT_VERSION, TurnKnowledgeSnapshot


def _meta(**overrides: object) -> dict:
    base: dict = {
        "version": SNAPSHOT_VERSION,
        "snapshot_id": "turn_abc",
        "profile_id": "default",
        "conversation_id": "42",
        "query": "Où est le mail ?",
        "interaction_mode": "agentic",
        "created_at": "2026-09-25T10:00:00+00:00",
        "retrieval_context": (
            "[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]\ncorps\n"
            "[/UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]"
        ),
        "retrieval_status": {
            "status": "ok",
            "verified_sources": ["email"],
            "partial_sources": [],
            "unavailable_sources": [],
            "latency_ms": 10,
        },
        "retrieval_references": [
            {"uid": "email:1", "source_type": "email", "secret": "drop"}
        ],
        "conversation_history": [{"role": "user", "content": "salut"}],
        "live_status": {"email": "ok", "calendar": "hacked"},
    }
    base.update(overrides)
    return base


def test_from_metadata_round_trip_and_allowlists() -> None:
    snapshot = TurnKnowledgeSnapshot.from_metadata(
        _meta(), expected_profile_id="default"
    )
    assert snapshot is not None
    assert snapshot.snapshot_id == "turn_abc"
    assert snapshot.retrieval_status["status"] == "ok"
    assert snapshot.retrieval_references[0] == {
        "uid": "email:1",
        "source_type": "email",
    }
    assert "secret" not in snapshot.retrieval_references[0]
    assert dict(snapshot.live_status) == {"email": "ok"}


def test_from_metadata_refuses_cross_profile() -> None:
    with pytest.raises(PermissionError, match="turn_snapshot_cross_profile"):
        TurnKnowledgeSnapshot.from_metadata(
            _meta(), expected_profile_id="other-profile"
        )


def test_from_metadata_rejects_bad_shape_or_version() -> None:
    assert (
        TurnKnowledgeSnapshot.from_metadata("nope", expected_profile_id="default")
        is None
    )
    assert (
        TurnKnowledgeSnapshot.from_metadata(
            _meta(version=99), expected_profile_id="default"
        )
        is None
    )
    assert (
        TurnKnowledgeSnapshot.from_metadata(
            _meta(snapshot_id=""), expected_profile_id="default"
        )
        is None
    )


def test_from_metadata_coerces_invalid_status_and_wraps_raw_retrieval() -> None:
    snapshot = TurnKnowledgeSnapshot.from_metadata(
        _meta(
            retrieval_status={"status": "hacked", "extra": "ignore"},
            retrieval_context="texte brut sans marqueur",
            retrieval_references=[{"uid": "a"}] * 20
            + [{"evil": "x"}, "skip-me"],
        ),
        expected_profile_id="default",
    )
    assert snapshot is not None
    assert snapshot.retrieval_status["status"] == "unavailable"
    assert "[UNTRUSTED_DATA:" in snapshot.retrieval_context
    assert "texte brut" in snapshot.retrieval_context
    assert len(snapshot.retrieval_references) == 8
