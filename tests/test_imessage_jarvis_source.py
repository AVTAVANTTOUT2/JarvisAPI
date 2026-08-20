"""Miroir iMessage = source unique de lecture ; watcher + dirty flag."""

from __future__ import annotations

import select
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import database
from integrations.imessage_import import (
    ImportResult,
    IMessageImporter,
    _DIRTY,
    _SYNC_LOCK,
)
from integrations.imessage_reader import IMessageReader
from jarvis.ingestion.imessage_watch import (
    IMessageFileWatcher,
    KqueueWatchBackend,
    NullWatchBackend,
)


def _init_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    from database import init_db

    init_db()
    return db_path


def _insert_message(
    *,
    apple_rowid: int,
    text: str,
    handle: str,
    is_from_me: int = 0,
    display_name: str = "",
    identity_name: str | None = None,
    guid: str | None = None,
) -> None:
    from database import get_db

    with get_db() as conn:
        identity_id = None
        if identity_name:
            conn.execute(
                """INSERT OR IGNORE INTO contact_identities
                   (identity_type, normalized_value, display_name, source)
                   VALUES ('imessage', ?, ?, 'test')""",
                (handle, identity_name),
            )
            identity_id = conn.execute(
                """SELECT id FROM contact_identities
                   WHERE identity_type = 'imessage' AND normalized_value = ?""",
                (handle,),
            ).fetchone()["id"]
        row = conn.execute(
            "SELECT id FROM imessage_handles WHERE handle = ?", (handle,)
        ).fetchone()
        if row:
            handle_id = int(row["id"])
        else:
            conn.execute(
                """INSERT INTO imessage_handles
                   (apple_handle_id, handle, display_name, contact_identity_id)
                   VALUES (?, ?, ?, ?)""",
                (apple_rowid, handle, display_name, identity_id),
            )
            handle_id = int(
                conn.execute(
                    "SELECT id FROM imessage_handles WHERE handle = ?", (handle,)
                ).fetchone()["id"]
            )
        conn.execute(
            """INSERT INTO imessage_messages
               (apple_rowid, guid, handle_id, text, date, is_from_me)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                apple_rowid,
                guid or f"guid-{apple_rowid}",
                handle_id,
                text,
                800_000_000 + apple_rowid,
                is_from_me,
            ),
        )


@pytest.mark.skipif(
    not hasattr(select, "kqueue"), reason="kqueue Darwin uniquement"
)
@pytest.mark.parametrize("fflag_name", ["KQ_NOTE_DELETE", "KQ_NOTE_RENAME"])
def test_kqueue_delete_or_rename_rearms_file_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fflag_name: str,
) -> None:
    """WAL recréé (DELETE/RENAME) : réouvrir les fds, ne pas garder l'inode mort."""
    target = tmp_path / "chat.db"
    target.write_text("x", encoding="utf-8")
    instances: list[object] = []
    fflag = getattr(select, fflag_name)

    class FakeKq:
        def __init__(self) -> None:
            self.polls = 0
            instances.append(self)

        def control(self, changelist, max_events, timeout=None):
            del max_events, timeout
            if changelist is not None:
                return []
            self.polls += 1
            if len(instances) == 1 and self.polls == 1:
                return [SimpleNamespace(fflags=fflag)]
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr(select, "kqueue", FakeKq)
    backend = KqueueWatchBackend()
    notified: list[int] = []
    backend.start([target], lambda: notified.append(1))
    deadline = time.monotonic() + 2.0
    while len(instances) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    backend.stop()
    assert notified == [1]
    assert len(instances) >= 2


def test_watcher_debounce_enqueues_once_then_again_after_new_event() -> None:
    jobs: list[str] = []
    watcher = IMessageFileWatcher(
        debounce_s=0.2,
        enqueue=lambda: jobs.append("watch"),
        backend=NullWatchBackend(),
    )
    watcher.notify(now=10.0)
    watcher.notify(now=10.05)
    assert watcher.tick(now=10.1) is False
    assert jobs == []
    assert watcher.tick(now=10.26) is True
    assert jobs == ["watch"]
    watcher.notify(now=10.3)
    assert watcher.tick(now=10.51) is True
    assert jobs == ["watch", "watch"]


def test_sync_marks_dirty_instead_of_dropping_when_lock_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = IMessageImporter()
    monkeypatch.setattr(importer, "is_available", lambda: True)
    monkeypatch.setattr(
        "integrations.imessage_import._requeue_dirty_sync", lambda: None
    )
    _DIRTY.clear()
    assert _SYNC_LOCK.acquire(blocking=False)
    try:
        result = importer.sync_incremental()
        assert result.errors == ["sync_already_running"]
        assert result.total_failed == 0
        assert _DIRTY.is_set()
    finally:
        _SYNC_LOCK.release()
        _DIRTY.clear()


def test_sync_repeats_while_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def locked() -> ImportResult:
        calls.append(1)
        if len(calls) == 1:
            _DIRTY.set()
        return ImportResult(mode="incremental")

    class FakeLock:
        def fileno(self) -> int:
            return 3

        def close(self) -> None:
            return None

    importer = IMessageImporter()
    monkeypatch.setattr(importer, "is_available", lambda: True)
    monkeypatch.setattr(importer, "_sync_incremental_locked", locked)
    monkeypatch.setattr(
        "integrations.imessage_import._try_acquire_process_sync_lock",
        lambda: FakeLock(),
    )
    monkeypatch.setattr("integrations.imessage_import.fcntl.flock", lambda *a, **k: None)
    monkeypatch.setattr(
        "integrations.imessage_import._requeue_dirty_sync", lambda: None
    )
    _DIRTY.clear()
    result = importer.sync_incremental()
    assert len(calls) == 2
    assert result.errors == []
    assert not _DIRTY.is_set()


def test_reader_and_consumers_do_not_open_chat_db() -> None:
    root = Path(__file__).resolve().parents[1]
    reader = (root / "integrations" / "imessage_reader.py").read_text(encoding="utf-8")
    bridge = (root / "integrations" / "imessage.py").read_text(encoding="utf-8")
    daemon = (root / "scripts" / "jarvis_daemon.py").read_text(encoding="utf-8")
    tv = (root / "tv" / "data_sources" / "messages.py").read_text(encoding="utf-8")
    live = (root / "jarvis" / "retrieval" / "live_sources.py").read_text(
        encoding="utf-8"
    )
    assert "AppleDataService" not in reader
    assert "AppleDataService" not in bridge
    assert "from integrations.apple_data import apple_data" not in daemon
    assert "apple_data.get_" not in tv
    assert "IMessageImporter" not in live
    assert "sync_incremental" not in live


def test_reader_is_available_without_chat_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_db(tmp_path, monkeypatch)
    reader = IMessageReader()
    assert reader.is_available() is True
    assert reader.count_messages() == 0


def test_recent_conversation_includes_sent_and_keeps_latest_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_db(tmp_path, monkeypatch)
    handle = "+33611111111"
    for index in range(1, 8):
        _insert_message(
            apple_rowid=index,
            text=f"msg-{index}",
            handle=handle,
            is_from_me=index % 2,
        )
    reader = IMessageReader()
    rows = reader.get_recent_conversation(handle, limit=5)
    assert [row["text"] for row in rows] == [
        "msg-3",
        "msg-4",
        "msg-5",
        "msg-6",
        "msg-7",
    ]
    assert any(row["is_from_me"] for row in rows)
    assert any(not row["is_from_me"] for row in rows)


def test_conversation_with_matches_identity_not_foreign_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_db(tmp_path, monkeypatch)
    _insert_message(
        apple_rowid=1,
        text="Dis à Bertille que j'arrive",
        handle="+33600000001",
        identity_name="Grégoire",
    )
    _insert_message(
        apple_rowid=2,
        text="On se voit ce soir",
        handle="+33600000002",
        identity_name="Bertille",
        is_from_me=0,
    )
    _insert_message(
        apple_rowid=3,
        text="J'arrive",
        handle="+33600000002",
        is_from_me=1,
    )
    reader = IMessageReader()
    rows = reader.get_conversation_with("Bertille", limit=10)
    assert [row["text"] for row in rows] == ["On se voit ce soir", "J'arrive"]
    gregoire = reader.get_conversation_with("Grégoire", limit=10)
    assert [row["text"] for row in gregoire] == ["Dis à Bertille que j'arrive"]
