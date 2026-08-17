"""Enregistrement durable : fsync avant ACK, reprise et rétention audio."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "recordings.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def test_recording_chunks_are_persisted_before_the_session_is_queued(
    tmp_db, tmp_path, monkeypatch
) -> None:
    from audio import recording_spool as spool_module
    from audio.continuous_recorder import ContinuousRecording
    from database import create_conversation, get_recording_session, list_ingestion_jobs

    monkeypatch.setattr(spool_module, "_SPOOL_ROOT", tmp_path / "spool")
    recording = ContinuousRecording(conversation_id=create_conversation(agent="voice"))
    session_id = recording.start("Réunion Orion")
    recording.add_chunk(b"a" * 4096)

    session = get_recording_session(session_id)
    assert session is not None
    assert session.state == "capturing"
    assert session.size_bytes == 4096
    assert len(recording.spool.chunk_paths()) == 1
    assert recording.spool.chunk_paths()[0].read_bytes() == b"a" * 4096

    result = recording.queue_for_processing()
    assert result["queued"] is True
    assert get_recording_session(session_id).state == "queued"
    jobs = list_ingestion_jobs(source="recording")
    assert [(job.job_kind, job.payload["session_id"]) for job in jobs] == [
        ("recording_process", session_id)
    ]


def test_recording_session_and_job_enqueue_are_atomic(
    tmp_db, tmp_path, monkeypatch
) -> None:
    from audio import recording_spool as spool_module
    from audio.continuous_recorder import ContinuousRecording
    from database import create_conversation, get_recording_session, list_ingestion_jobs

    monkeypatch.setattr(spool_module, "_SPOOL_ROOT", tmp_path / "spool")
    recording = ContinuousRecording(conversation_id=create_conversation(agent="voice"))
    session_id = recording.start("Réunion atomique")
    recording.add_chunk(b"a" * 4096)

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("job store unavailable")

    monkeypatch.setattr(spool_module, "enqueue_ingestion_job", fail_enqueue)
    with pytest.raises(RuntimeError, match="job store unavailable"):
        recording.queue_for_processing()

    session = get_recording_session(session_id)
    assert session is not None
    assert session.state == "capturing"
    assert list_ingestion_jobs(source="recording") == []


@pytest.mark.asyncio
async def test_partial_stt_never_marks_recording_complete() -> None:
    from audio.continuous_recorder import ContinuousRecording

    class PartialSTT:
        calls = 0

        async def transcribe(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("segment failed")
            return "premier segment"

    recording = ContinuousRecording(conversation_id=None)
    recording.audio_chunks = [b"a" * 1024, b"b" * 1024]

    with pytest.raises(RuntimeError, match="recording_stt_partial"):
        await recording._transcribe_all(PartialSTT(), None)


def test_raw_recording_is_purged_only_after_seven_day_retention(
    tmp_db, tmp_path, monkeypatch
) -> None:
    from audio import recording_spool as spool_module
    from database import (
        create_conversation,
        get_recording_session,
        update_recording_session,
    )

    monkeypatch.setattr(spool_module, "_SPOOL_ROOT", tmp_path / "spool")
    spool = spool_module.RecordingSpool.create(
        conversation_id=create_conversation(agent="voice"),
        label="Note",
    )
    spool.append(b"b" * 4096)
    spool.mark_succeeded(transcript="texte conservé", summary="résumé conservé")

    assert spool_module.purge_recording_audio(spool.session_id) is False
    update_recording_session(
        spool.session_id,
        retention_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    assert spool_module.purge_recording_audio(spool.session_id) is True
    session = get_recording_session(spool.session_id)
    assert session is not None
    assert session.state == "expired"
    assert session.transcript == "texte conservé"
    assert session.summary == "résumé conservé"
    assert not spool.path.exists()


def test_recording_purge_rejects_symlinked_session_dir(
    tmp_db, tmp_path, monkeypatch
) -> None:
    from audio import recording_spool as spool_module
    from database import create_conversation, update_recording_session

    monkeypatch.setattr(spool_module, "_SPOOL_ROOT", tmp_path / "spool")
    spool = spool_module.RecordingSpool.create(
        conversation_id=create_conversation(agent="voice"),
        label="Note",
    )
    spool.mark_succeeded(transcript="texte", summary="résumé")
    update_recording_session(
        spool.session_id,
        retention_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    target = spool.path.with_name("target")
    spool.path.rename(target)
    spool.path.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="recording_spool_path_invalid"):
        spool_module.purge_recording_audio(spool.session_id)
    assert target.is_dir()


def test_recording_actions_are_proposals_not_implicit_effects() -> None:
    from audio.continuous_recorder import ContinuousRecording

    proposals = ContinuousRecording._proposal_summary(
        {
            "tasks": [{"title": "Envoyer le devis"}],
            "calendar_events": [{"summary": "Appel", "date": "2026-08-17"}],
        }
    )
    assert proposals["requires_approval"] is True
    assert proposals["tasks_proposed"] == 1
    assert proposals["events_proposed"] == 1
    assert "tasks_created" not in proposals


@pytest.mark.asyncio
async def test_recording_derivatives_are_idempotent_for_one_spool_session(
    tmp_db, monkeypatch
) -> None:
    import config
    from audio.continuous_recorder import ContinuousRecording
    from database import (
        create_recording_session,
        get_db,
        save_conversation_turns,
        save_episode,
        save_recording,
    )

    monkeypatch.setattr(config, "DESKTOP_NOTIFICATIONS", False)
    session = create_recording_session(
        spool_path=str(tmp_db.parent / "session"),
        state="processing",
    )
    payload = {
        "conversation_id": None,
        "label": "Orion",
        "duration_seconds": 42,
        "transcription": "Décision Orion",
        "summary": "Résumé Orion",
        "synthesis": {"title": "Orion", "summary": "Résumé Orion"},
        "actions": {"requires_approval": True},
        "audio_size_kb": 8,
        "recording_session_id": session.id,
    }
    recording_id = save_recording(**payload)
    assert save_recording(**payload) == recording_id

    episode_id = save_episode(
        "recording",
        "Résumé Orion",
        summary="Orion",
        recording_id=recording_id,
    )
    assert (
        save_episode(
            "recording",
            "Résumé différent ignoré",
            summary="Orion bis",
            recording_id=recording_id,
        )
        == episode_id
    )

    save_conversation_turns(
        recording_id,
        [
            {"speaker_label": "A", "text": "premier"},
            {"speaker_label": "B", "text": "second"},
        ],
    )
    save_conversation_turns(
        recording_id,
        [{"speaker_label": "A", "text": "premier corrigé"}],
    )

    recording = ContinuousRecording(conversation_id=None)
    recording.label = "Orion"
    synthesis = {
        "title": "Orion",
        "summary": "Résumé Orion",
        "tasks": [{"title": "Valider"}],
        "calendar_events": [],
    }
    await recording._apply_synthesis(
        synthesis,
        recording_id=recording_id,
        session_id=session.id,
    )
    await recording._apply_synthesis(
        synthesis,
        recording_id=recording_id,
        session_id=session.id,
    )

    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        turns = conn.execute(
            "SELECT turn_order, text FROM conversation_turns ORDER BY turn_order"
        ).fetchall()
    assert [(row["turn_order"], row["text"]) for row in turns] == [
        (0, "premier corrigé")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ["completed", "ready"])
async def test_terminal_recording_job_replay_skips_stt_and_reenqueue(
    tmp_db, monkeypatch, terminal_state
) -> None:
    from audio.continuous_recorder import (
        ContinuousRecording,
        process_recording_ingestion_job,
    )
    from audio.recording_spool import reconcile_recording_sessions
    from database import create_recording_session, get_recording_session

    session = create_recording_session(
        spool_path=str(tmp_db.parent / f"terminal-{terminal_state}"),
        state=terminal_state,
    )

    def unexpected_reopen(*_args, **_kwargs):
        raise AssertionError("terminal recording was reopened")

    monkeypatch.setattr(ContinuousRecording, "from_spool", unexpected_reopen)
    result = await process_recording_ingestion_job(
        SimpleNamespace(payload={"session_id": session.id}),
        None,
        None,
    )

    assert result.status == "ok"
    assert reconcile_recording_sessions() == 0
    assert get_recording_session(session.id).state == "completed"


def test_desktop_notification_claim_is_at_most_once(tmp_db) -> None:
    from database import (
        claim_recording_desktop_notification,
        create_recording_session,
        get_recording_session,
    )

    session = create_recording_session(
        spool_path=str(tmp_db.parent / "desktop-claim"),
        state="processing",
    )

    assert claim_recording_desktop_notification(session.id) is True
    assert claim_recording_desktop_notification(session.id) is False
    assert get_recording_session(session.id).desktop_notification_claimed_at is not None
