"""Vertical accéléré des captures longues : ACK, reprise et RAM bornée."""

from __future__ import annotations

import errno
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _webm_segment(sequence: int, size: int = 4096) -> bytes:
    marker = sequence.to_bytes(4, "big")
    return (b"\x1aE\xdf\xa3" + marker + b"a" * size)[:size]


def _headers(payload: bytes, *, duration_ms: int = 30_000) -> dict[str, str]:
    return {
        "Content-Type": "audio/webm;codecs=opus",
        "X-Chunk-SHA256": hashlib.sha256(payload).hexdigest(),
        "X-Chunk-Duration-Ms": str(duration_ms),
        "X-Recording-Protocol-Version": "1",
    }


@pytest.fixture
def recording_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import config
    import database
    from audio import recording_spool

    db_path = tmp_path / "recordings.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(recording_spool, "_SPOOL_ROOT", tmp_path / "spool")
    database.init_db()
    return tmp_path


@pytest.mark.parametrize("minutes", [1, 30, 180])
def test_virtual_long_recordings_are_segmented_and_queued_once(
    recording_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    minutes: int,
) -> None:
    from audio import recording_spool
    from audio.continuous_recorder import ContinuousRecording
    from audio.recording_sessions import complete_recording_session
    from database import list_ingestion_jobs

    # La preuve virtuelle ne mesure pas le disque : elle garde les transitions
    # et les fichiers réels, mais neutralise seulement les fsync coûteux.
    monkeypatch.setattr(recording_spool.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(recording_spool, "_fsync_dir", lambda _path: None)
    spool = recording_spool.RecordingSpool.create(
        conversation_id=None,
        label=f"Virtuel {minutes} min",
        client_recording_id=str(uuid4()),
    )
    segment_count = minutes * 2
    for sequence in range(segment_count):
        payload = _webm_segment(sequence)
        ack = spool.append_chunk(
            sequence=sequence,
            audio_bytes=payload,
            expected_checksum=hashlib.sha256(payload).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm;codecs=opus",
        )
        assert ack["next_sequence"] == sequence + 1

    reopened = recording_spool.RecordingSpool.open(spool.session_id)
    recording = ContinuousRecording.from_spool(spool.session_id)
    assert recording.audio_chunks == []
    assert reopened.duration_ms == minutes * 60_000
    assert reopened.chunk_count == segment_count

    if minutes == 180:
        overflow = _webm_segment(segment_count)
        with pytest.raises(
            recording_spool.RecordingSpoolError,
            match="recording_duration_exceeded",
        ):
            reopened.append_chunk(
                sequence=segment_count,
                audio_bytes=overflow,
                expected_checksum=hashlib.sha256(overflow).hexdigest(),
                duration_ms=1,
                mime_type="audio/webm",
            )
        assert reopened.chunk_count == segment_count
        assert reopened.duration_ms == 180 * 60_000

    first = complete_recording_session(
        spool.session_id,
        expected_chunks=segment_count,
        duration_seconds=minutes * 60,
    )
    second = complete_recording_session(
        spool.session_id,
        expected_chunks=segment_count,
        duration_seconds=minutes * 60,
    )
    jobs = list_ingestion_jobs(source="recording")
    assert first["duration_seconds"] == minutes * 60
    assert second["idempotent"] is True
    assert len(jobs) == 1
    assert jobs[0].max_attempts == 3


@pytest.mark.asyncio
async def test_worker_reads_at_most_one_segment_at_a_time(recording_env: Path) -> None:
    from audio.continuous_recorder import ContinuousRecording
    from audio.recording_spool import RecordingSpool

    spool = RecordingSpool.create(
        conversation_id=None,
        label="RAM bornée",
        client_recording_id=str(uuid4()),
    )
    for sequence in range(4):
        payload = _webm_segment(sequence, size=4096)
        spool.append_chunk(
            sequence=sequence,
            audio_bytes=payload,
            expected_checksum=hashlib.sha256(payload).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )

    class STT:
        active = 0
        peak_active = 0
        peak_bytes = 0

        async def transcribe(self, payload, **_kwargs):
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.peak_bytes = max(self.peak_bytes, len(payload))
            self.active -= 1
            return "segment"

    recording = ContinuousRecording.from_spool(spool.session_id)
    stt = STT()
    transcript = await recording._transcribe_all(stt, None)

    assert transcript.count("segment") == 4
    assert recording.audio_chunks == []
    assert stt.peak_active == 1
    assert stt.peak_bytes == 4096


def test_lost_ack_resume_and_replay_are_idempotent(recording_env: Path) -> None:
    from audio.recording_spool import RecordingSpool, RecordingSpoolError

    spool = RecordingSpool.create(
        conversation_id=None,
        label="Crash ACK",
        client_recording_id=str(uuid4()),
    )
    payload = _webm_segment(0)
    checksum = hashlib.sha256(payload).hexdigest()
    assert spool.append_chunk(
        sequence=0,
        audio_bytes=payload,
        expected_checksum=checksum,
        duration_ms=30_000,
        mime_type="audio/webm",
    )["status"] == "accepted"

    # Le processus disparaît avant que le client reçoive l'ACK.
    resumed = RecordingSpool.open(spool.session_id)
    duplicate = resumed.append_chunk(
        sequence=0,
        audio_bytes=payload,
        expected_checksum=checksum,
        duration_ms=30_000,
        mime_type="audio/webm",
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["received_chunks"] == 1
    assert len(resumed.chunk_paths()) == 1

    conflicting = _webm_segment(99)
    with pytest.raises(RecordingSpoolError, match="recording_chunk_conflict"):
        resumed.append_chunk(
            sequence=0,
            audio_bytes=conflicting,
            expected_checksum=hashlib.sha256(conflicting).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )
    with pytest.raises(RecordingSpoolError, match="recording_chunk_gap"):
        resumed.append_chunk(
            sequence=2,
            audio_bytes=_webm_segment(2),
            expected_checksum=hashlib.sha256(_webm_segment(2)).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )


def test_complete_is_serialized_after_inflight_chunk(
    recording_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from audio import recording_spool
    from audio.recording_sessions import complete_recording_session
    from database import get_recording_session, list_ingestion_jobs

    spool = recording_spool.RecordingSpool.create(
        conversation_id=None,
        label="Course complete",
        client_recording_id=str(uuid4()),
    )
    payload = _webm_segment(0)
    append_paused = threading.Event()
    release_append = threading.Event()
    original_update = recording_spool.update_recording_session

    def gated_update(session_id: str, **changes):
        if changes.get("state") == "capturing" and not append_paused.is_set():
            append_paused.set()
            assert release_append.wait(timeout=5)
        return original_update(session_id, **changes)

    monkeypatch.setattr(recording_spool, "update_recording_session", gated_update)
    with ThreadPoolExecutor(max_workers=2) as executor:
        append_future = executor.submit(
            spool.append_chunk,
            sequence=0,
            audio_bytes=payload,
            expected_checksum=hashlib.sha256(payload).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )
        assert append_paused.wait(timeout=5)
        complete_future = executor.submit(
            complete_recording_session,
            spool.session_id,
            expected_chunks=1,
            duration_seconds=30,
        )
        time.sleep(0.1)
        assert not complete_future.done()
        release_append.set()
        assert append_future.result(timeout=5)["status"] == "accepted"
        assert complete_future.result(timeout=5)["queued"] is True

    assert get_recording_session(spool.session_id).state == "queued"
    assert len(list_ingestion_jobs(source="recording")) == 1


def test_acked_chunk_corruption_blocks_sealing_and_processing(
    recording_env: Path,
) -> None:
    from audio.recording_spool import RecordingSpool, RecordingSpoolError
    from database import list_ingestion_jobs

    spool = RecordingSpool.create(
        conversation_id=None,
        label="Corruption disque",
        client_recording_id=str(uuid4()),
    )
    for sequence in range(2):
        payload = _webm_segment(sequence)
        spool.append_chunk(
            sequence=sequence,
            audio_bytes=payload,
            expected_checksum=hashlib.sha256(payload).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )
    spool.chunk_paths()[0].write_bytes(b"bad")

    with pytest.raises(RecordingSpoolError, match="recording_chunk_corrupt"):
        spool.verify_integrity()
    with pytest.raises(RecordingSpoolError, match="recording_chunk_corrupt"):
        spool.seal_and_enqueue(
            label="Corruption disque",
            duration_seconds=60,
            expected_chunks=2,
        )
    assert list_ingestion_jobs(source="recording") == []


def test_final_expired_recording_lease_terminalizes_session(
    recording_env: Path,
) -> None:
    from audio.recording_spool import RecordingSpool
    from database import (
        claim_ingestion_jobs,
        get_db,
        get_recording_session,
        list_ingestion_jobs,
        update_recording_session,
    )

    spool = RecordingSpool.create(
        conversation_id=None,
        label="Worker crash",
        client_recording_id=str(uuid4()),
    )
    payload = _webm_segment(0)
    spool.append_chunk(
        sequence=0,
        audio_bytes=payload,
        expected_checksum=hashlib.sha256(payload).hexdigest(),
        duration_ms=30_000,
        mime_type="audio/webm",
    )
    spool.enqueue(label="Worker crash", duration_seconds=30)
    job_id = list_ingestion_jobs(source="recording")[0].id

    for attempt in range(3):
        claimed = claim_ingestion_jobs(
            f"worker-{attempt}",
            handler_pairs=[("recording", "recording_process")],
        )
        assert len(claimed) == 1
        with get_db() as connection:
            connection.execute(
                "UPDATE ingestion_jobs SET lease_expires_at = '1970-01-01 00:00:00' WHERE id = ?",
                (job_id,),
            )
    update_recording_session(spool.session_id, state="processing")
    assert claim_ingestion_jobs(
        "worker-final",
        handler_pairs=[("recording", "recording_process")],
    ) == []

    assert list_ingestion_jobs(source="recording")[0].status == "dead"
    session = get_recording_session(spool.session_id)
    assert session.state == "failed"
    assert session.error == "recording_worker_crashed"
    assert session.attempts == 3


def test_crash_after_chunk_fsync_recovers_orphan_without_duplicate(
    recording_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from audio.recording_spool import RecordingSpool

    spool = RecordingSpool.create(
        conversation_id=None,
        label="Crash manifeste",
        client_recording_id=str(uuid4()),
    )
    payload = _webm_segment(0)
    checksum = hashlib.sha256(payload).hexdigest()
    original_write_state = RecordingSpool._write_state
    crashed = False

    def crash_once(current: RecordingSpool) -> None:
        nonlocal crashed
        if current.next_sequence == 1 and not crashed:
            crashed = True
            raise RuntimeError("process_crashed_after_chunk_fsync")
        original_write_state(current)

    monkeypatch.setattr(RecordingSpool, "_write_state", crash_once)
    with pytest.raises(RuntimeError, match="process_crashed_after_chunk_fsync"):
        spool.append_chunk(
            sequence=0,
            audio_bytes=payload,
            expected_checksum=checksum,
            duration_ms=30_000,
            mime_type="audio/webm",
        )

    monkeypatch.setattr(RecordingSpool, "_write_state", original_write_state)
    reopened = RecordingSpool.open(spool.session_id)
    recovered = reopened.append_chunk(
        sequence=0,
        audio_bytes=payload,
        expected_checksum=checksum,
        duration_ms=30_000,
        mime_type="audio/webm",
    )

    assert recovered["status"] == "duplicate"
    assert recovered["next_sequence"] == 1
    assert len(reopened.chunk_paths()) == 1


def test_concurrent_start_reuses_one_durable_session(recording_env: Path) -> None:
    from audio.recording_sessions import start_recording_session

    client_recording_id = str(uuid4())

    def start() -> str:
        return start_recording_session(
            client_recording_id=client_recording_id,
            conversation_id=None,
            label="Départ concurrent",
        )["session_id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        session_ids = list(executor.map(lambda _index: start(), range(8)))

    assert len(set(session_ids)) == 1


def test_invalid_truncated_and_full_storage_segments_fail_closed(
    recording_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from audio import recording_spool

    spool = recording_spool.RecordingSpool.create(
        conversation_id=None,
        label="Erreurs",
        client_recording_id=str(uuid4()),
    )
    payload = _webm_segment(0)
    with pytest.raises(recording_spool.RecordingSpoolError, match="checksum_mismatch"):
        spool.append_chunk(
            sequence=0,
            audio_bytes=payload,
            expected_checksum="0" * 64,
            duration_ms=30_000,
            mime_type="audio/webm",
        )
    truncated = b"\x1aE\xdf\xa3short"
    with pytest.raises(recording_spool.RecordingSpoolError, match="chunk_truncated"):
        spool.append_chunk(
            sequence=0,
            audio_bytes=truncated,
            expected_checksum=hashlib.sha256(truncated).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )
    with pytest.raises(recording_spool.RecordingSpoolError, match="container_invalid"):
        spool.append_chunk(
            sequence=0,
            audio_bytes=b"not-webm".ljust(1024, b"x"),
            expected_checksum=hashlib.sha256(b"not-webm".ljust(1024, b"x")).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )

    original_write = recording_spool.os.write

    def no_space(descriptor: int, data) -> int:
        if len(data) >= 800:
            raise OSError(errno.ENOSPC, "full")
        return original_write(descriptor, data)

    monkeypatch.setattr(recording_spool.os, "write", no_space)
    with pytest.raises(recording_spool.RecordingSpoolError, match="storage_full"):
        spool.append_chunk(
            sequence=0,
            audio_bytes=payload,
            expected_checksum=hashlib.sha256(payload).hexdigest(),
            duration_ms=30_000,
            mime_type="audio/webm",
        )
    assert spool.chunk_paths() == []


def test_recording_session_http_contract_resume_cancel_and_bounds(
    recording_env: Path,
) -> None:
    from api.router_recordings import router
    from api.request_limits import request_size_limit
    from database import get_recording_session, update_recording_session

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client_id = str(uuid4())
    created = client.post(
        "/api/recording-sessions",
        json={"client_recording_id": client_id, "label": "Cours"},
    )
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert client.post(
        "/api/recording-sessions",
        json={"client_recording_id": client_id, "label": "Cours"},
    ).json()["session_id"] == session_id

    payload = _webm_segment(0)
    accepted = client.put(
        f"/api/recording-sessions/{session_id}/chunks/0",
        content=payload,
        headers=_headers(payload),
    )
    assert accepted.status_code == 200
    assert accepted.json()["protocol_version"] == 1
    assert accepted.json()["status"] == "accepted"
    duplicate = client.put(
        f"/api/recording-sessions/{session_id}/chunks/0",
        content=payload,
        headers=_headers(payload),
    )
    assert duplicate.json()["status"] == "duplicate"
    assert client.get(f"/api/recording-sessions/{session_id}").json()[
        "next_sequence"
    ] == 1
    unsupported_headers = _headers(_webm_segment(1))
    unsupported_headers["X-Recording-Protocol-Version"] = "2"
    assert client.put(
        f"/api/recording-sessions/{session_id}/chunks/1",
        content=_webm_segment(1),
        headers=unsupported_headers,
    ).status_code == 426
    assert request_size_limit(
        "PUT", f"/api/recording-sessions/{session_id}/chunks/0"
    ) == 20 * 1024 * 1024

    incomplete = client.post(
        f"/api/recording-sessions/{session_id}/complete",
        json={"expected_chunks": 2, "duration_seconds": 60},
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"]["code"] == "recording_chunks_incomplete"
    assert client.get(f"/api/recording-sessions/{session_id}").json()[
        "state"
    ] == "capturing"

    cancelled = client.delete(f"/api/recording-sessions/{session_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert client.delete(f"/api/recording-sessions/{session_id}").json()[
        "state"
    ] == "cancelled"
    assert get_recording_session(session_id).spool_path == ""
    assert list((recording_env / "spool").rglob("*.chunk")) == []
    rejected_after_cancel = client.put(
        f"/api/recording-sessions/{session_id}/chunks/1",
        content=_webm_segment(1),
        headers=_headers(_webm_segment(1)),
    )
    assert rejected_after_cancel.status_code == 409
    assert rejected_after_cancel.json()["detail"]["code"] == "recording_spool_unavailable"

    retry_id = str(uuid4())
    retry_session = client.post(
        "/api/recording-sessions",
        json={"client_recording_id": retry_id, "label": "Retry"},
    ).json()["session_id"]
    retry_payload = _webm_segment(0)
    client.put(
        f"/api/recording-sessions/{retry_session}/chunks/0",
        content=retry_payload,
        headers=_headers(retry_payload),
    )
    update_recording_session(
        retry_session,
        state="retry",
        attempts=2,
        error="recording_stt_timeout",
    )
    retried = client.post(
        f"/api/recording-sessions/{retry_session}/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["state"] == "queued"
    sealed_cancel = client.delete(f"/api/recording-sessions/{retry_session}")
    assert sealed_cancel.status_code == 409
    assert sealed_cancel.json()["detail"]["code"] == "recording_session_not_cancellable"
    update_recording_session(
        retry_session,
        state="failed",
        attempts=3,
        error="recording_stt_timeout",
    )
    assert client.post(
        f"/api/recording-sessions/{retry_session}/retry"
    ).status_code == 409


@pytest.mark.asyncio
async def test_stt_timeout_is_retryable_and_creates_no_derivative(
    recording_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from audio import recording_spool, stt
    from audio.continuous_recorder import (
        RecordingProcessingError,
        process_recording_ingestion_job,
    )
    from database import get_db, get_recording_session, list_ingestion_jobs

    spool = recording_spool.RecordingSpool.create(
        conversation_id=None,
        label="Timeout",
        client_recording_id=str(uuid4()),
    )
    payload = _webm_segment(0)
    spool.append_chunk(
        sequence=0,
        audio_bytes=payload,
        expected_checksum=hashlib.sha256(payload).hexdigest(),
        duration_ms=30_000,
        mime_type="audio/webm",
    )
    spool.enqueue(label="Timeout", duration_seconds=30)
    job = replace(list_ingestion_jobs(source="recording")[0], attempts=1)

    async def timeout(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(stt, "available", True)
    monkeypatch.setattr(stt, "transcribe", timeout)
    with pytest.raises(RecordingProcessingError, match="recording_stt_timeout"):
        await process_recording_ingestion_job(job, None, None)

    session = get_recording_session(spool.session_id)
    assert session.state == "retry"
    assert session.error == "recording_stt_timeout"
    with get_db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
