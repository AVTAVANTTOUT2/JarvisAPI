"""Tests : tours de parole diarisés (conversation_turns) et attribution de locuteur."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _make_recording() -> int:
    from database import save_recording

    return save_recording(
        conversation_id=None,
        label="test",
        duration_seconds=120,
        transcription="Bonjour. Salut toi.",
        summary="Résumé test",
        synthesis={},
        actions={},
        audio_size_kb=10,
        title="Conversation test",
    )


def test_save_and_get_conversation_turns(tmp_db):
    from database import get_conversation_turns, save_conversation_turns

    rec_id = _make_recording()
    turns = [
        {"speaker_label": "A", "text": "Bonjour", "start_ms": 0, "end_ms": 500},
        {"speaker_label": "B", "text": "Salut toi", "start_ms": 600, "end_ms": 1200},
    ]
    inserted = save_conversation_turns(rec_id, turns)
    assert inserted == 2

    stored = get_conversation_turns(rec_id)
    assert len(stored) == 2
    assert stored[0]["speaker_label"] == "A"
    assert stored[0]["text"] == "Bonjour"
    assert stored[1]["speaker_label"] == "B"


def test_turns_preserve_insertion_order(tmp_db):
    from database import get_conversation_turns, save_conversation_turns

    rec_id = _make_recording()
    turns = [
        {"speaker_label": "A", "text": "un", "start_ms": 0, "end_ms": 100},
        {"speaker_label": "B", "text": "deux", "start_ms": 100, "end_ms": 200},
        {"speaker_label": "A", "text": "trois", "start_ms": 200, "end_ms": 300},
    ]
    save_conversation_turns(rec_id, turns)
    stored = get_conversation_turns(rec_id)
    assert [t["text"] for t in stored] == ["un", "deux", "trois"]


def test_get_unlabeled_speakers(tmp_db):
    from database import get_unlabeled_speakers, save_conversation_turns

    rec_id = _make_recording()
    save_conversation_turns(rec_id, [
        {"speaker_label": "A", "text": "un", "start_ms": 0, "end_ms": 100},
        {"speaker_label": "B", "text": "deux", "start_ms": 100, "end_ms": 200},
        {"speaker_label": "A", "text": "trois", "start_ms": 200, "end_ms": 300},
    ])
    unlabeled = get_unlabeled_speakers(rec_id)
    assert unlabeled == ["A", "B"]


def test_assign_speaker_to_person_updates_all_their_turns(tmp_db):
    from database import (
        assign_speaker_to_person,
        get_conversation_turns,
        get_db,
        get_unlabeled_speakers,
        save_conversation_turns,
    )

    rec_id = _make_recording()
    save_conversation_turns(rec_id, [
        {"speaker_label": "A", "text": "un", "start_ms": 0, "end_ms": 100},
        {"speaker_label": "B", "text": "deux", "start_ms": 100, "end_ms": 200},
        {"speaker_label": "A", "text": "trois", "start_ms": 200, "end_ms": 300},
    ])
    with get_db() as conn:
        cur = conn.execute("INSERT INTO people (name) VALUES ('Karim')")
        person_id = cur.lastrowid

    updated = assign_speaker_to_person(rec_id, "A", person_id)
    assert updated == 2  # les 2 tours de "A"

    turns = get_conversation_turns(rec_id)
    a_turns = [t for t in turns if t["speaker_label"] == "A"]
    assert all(t["person_name"] == "Karim" for t in a_turns)

    remaining_unlabeled = get_unlabeled_speakers(rec_id)
    assert remaining_unlabeled == ["B"]


def test_unlabeled_speakers_empty_for_unknown_recording(tmp_db):
    from database import get_unlabeled_speakers

    assert get_unlabeled_speakers(999999) == []


def test_conversation_turns_scoped_per_recording(tmp_db):
    from database import get_conversation_turns, save_conversation_turns

    rec1 = _make_recording()
    rec2 = _make_recording()
    save_conversation_turns(rec1, [{"speaker_label": "A", "text": "rec1", "start_ms": 0, "end_ms": 100}])
    save_conversation_turns(rec2, [{"speaker_label": "A", "text": "rec2", "start_ms": 0, "end_ms": 100}])

    assert [t["text"] for t in get_conversation_turns(rec1)] == ["rec1"]
    assert [t["text"] for t in get_conversation_turns(rec2)] == ["rec2"]
