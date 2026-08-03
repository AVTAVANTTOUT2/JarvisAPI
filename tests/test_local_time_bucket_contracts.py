"""Contrats UTC → journées civiles locales pour les requêtes SQLite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture()
def paris_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "paris-time-buckets.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.TIMEZONE", "Europe/Paris")
    monkeypatch.setattr("config.RITUALS_TTS", False)

    from database import init_db

    init_db()
    return db_path


def test_local_day_bounds_include_only_the_dst_day(paris_db: Path) -> None:
    from database import get_db, get_daily_messages
    from database.time_buckets import utc_bounds_for_local_day

    assert utc_bounds_for_local_day("2026-03-29") == (
        "2026-03-28 23:00:00",
        "2026-03-29 22:00:00",
    )
    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id) VALUES (1)")
        for timestamp in (
            "2026-03-28 22:59:59",
            "2026-03-28 23:00:00",
            "2026-03-29 21:59:59",
            "2026-03-29 22:00:00",
        ):
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at)
                VALUES (1, 'user', ?, ?)
                """,
                (timestamp, timestamp),
            )

    assert [row["content"] for row in get_daily_messages("2026-03-29")] == [
        "2026-03-28 23:00:00",
        "2026-03-29 21:59:59",
    ]


def test_time_machine_converts_utc_timestamps_for_display(paris_db: Path) -> None:
    from database import get_db
    from scripts.time_machine import build_day_timeline

    with get_db() as conn:
        conn.execute("""
            INSERT INTO mood_log (mood_score, energy_level, created_at)
            VALUES (7, 6, '2026-07-09 22:30:00')
            """)
        conn.execute("""
            INSERT INTO mood_log (mood_score, energy_level, created_at)
            VALUES (1, 1, '2026-07-10 22:00:00')
            """)

    result = build_day_timeline("2026-07-10")
    moods = [event for event in result["timeline"] if event["type"] == "mood"]
    assert len(moods) == 1
    assert moods[0]["time"] == "00:30"


def test_mood_signal_uses_local_night_hours_not_utc_hours(paris_db: Path) -> None:
    from database import get_db
    from scripts.rituals import compute_mood_signal

    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        conn.execute("""
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (1, 'user', 'inside', '2026-07-09 22:30:00')
            """)
        conn.execute("""
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (1, 'user', 'outside', '2026-07-09 21:59:59')
            """)
        for minute in range(10):
            conn.execute(
                """
                INSERT INTO screen_activity (device, app, created_at)
                VALUES ('mac', 'code', ?)
                """,
                (f"2026-07-10 21:{minute:02d}:00",),
            )

    signal = compute_mood_signal("2026-07-10")
    assert signal["msg_count"] == 1
    assert "activite_nocturne" in signal["flags"]


def test_runtime_queries_never_bucket_created_at_with_sqlite_date() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for directory in ("database", "scripts", "api"):
        for path in (root / directory).rglob("*.py"):
            source = path.read_text(encoding="utf-8").lower().replace(" ", "")
            if "date(created_at)" in source or "date(m.created_at)" in source:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_sqlite_utc_parser_accepts_aware_values(
    paris_db: Path,
) -> None:
    from database.time_buckets import sqlite_utc_to_local

    converted = sqlite_utc_to_local(datetime.fromisoformat("2026-10-25T00:30:00+00:00"))
    assert converted.isoformat() == "2026-10-25T02:30:00+02:00"
