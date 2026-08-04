"""Contrats UTC des traces et métriques du pipeline vocal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture()
def voice_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import config
    import database

    db_path = tmp_path / "voice-debug.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "TIMEZONE", "Europe/Paris")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def test_voice_debug_writer_and_schema_use_utc(voice_db: Path) -> None:
    from database import _save_voice_debug_trace, get_db

    before = datetime.now(timezone.utc).replace(microsecond=0)
    trace_id = _save_voice_debug_trace(
        {
            "input_text": "bonjour",
            "latency_stt_ms": 100,
            "latency_total_ms": 500,
        }
    )
    after = datetime.now(timezone.utc).replace(microsecond=0)

    assert trace_id is not None
    with get_db() as conn:
        created_at = conn.execute(
            "SELECT created_at FROM voice_debug_log WHERE id = ?", (trace_id,)
        ).fetchone()[0]
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'voice_debug_log'"
        ).fetchone()[0]

    persisted = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
    assert before <= persisted <= after
    assert "CURRENT_TIMESTAMP" in table_sql
    assert "localtime" not in table_sql


def test_legacy_voice_debug_migration_is_idempotent(voice_db: Path) -> None:
    from database import get_db
    from database.migrations import _migrate_voice_debug_timestamps_to_utc

    with get_db() as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key = 'voice_debug_timestamp_utc_v1'"
        )
        trace_id = conn.execute(
            "INSERT INTO voice_debug_log (created_at, input_text) VALUES (?, ?)",
            ("2026-07-10 00:30:00", "legacy"),
        ).lastrowid

        _migrate_voice_debug_timestamps_to_utc(conn)
        first = conn.execute(
            "SELECT created_at FROM voice_debug_log WHERE id = ?", (trace_id,)
        ).fetchone()[0]
        _migrate_voice_debug_timestamps_to_utc(conn)
        second = conn.execute(
            "SELECT created_at FROM voice_debug_log WHERE id = ?", (trace_id,)
        ).fetchone()[0]

    assert first == "2026-07-09 22:30:00"
    assert second == first


def test_voice_latency_window_compares_utc_instants(voice_db: Path) -> None:
    from database import get_db, get_voice_latency_metrics

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    old = (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO voice_debug_log
               (created_at, input_text, latency_stt_ms, latency_total_ms)
               VALUES (?, ?, ?, ?)""",
            (
                (recent, "recent", 120, 500),
                (old, "old", 999, 999),
            ),
        )

    metrics = get_voice_latency_metrics(days=7)
    assert metrics["samples"] == 1
    assert metrics["stages"]["stt"] == {
        "p50_ms": 120,
        "p95_ms": 120,
        "count": 1,
    }
