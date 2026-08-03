"""Contrats UTC → journées civiles locales pour les requêtes SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
    from database import get_daily_messages, get_db
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


def test_time_machine_keeps_true_order_during_repeated_dst_hour(paris_db: Path) -> None:
    from database import get_db
    from scripts.time_machine import build_day_timeline

    with get_db() as conn:
        conn.execute(
            "INSERT INTO screen_activity (app, notable, created_at) "
            "VALUES ('Code', 'avant le repli', '2026-10-25 00:45:00')"
        )
        conn.execute(
            "INSERT INTO mood_log (mood_score, energy_level, created_at) "
            "VALUES (7, 6, '2026-10-25 01:15:00')"
        )

    events = build_day_timeline("2026-10-25")["timeline"]
    assert [(event["type"], event["time"]) for event in events] == [
        ("screen_notable", "02:45"),
        ("mood", "02:15"),
    ]


def test_local_day_consumers_share_exclusive_utc_bounds(paris_db: Path) -> None:
    from database import get_db
    from scripts.day_scoring import _day_summary_for_scoring
    from scripts.jarvis_journal import _day_facts
    from scripts.rituals import _day_snapshot
    from scripts.time_machine import build_day_timeline

    with get_db() as conn:
        place_id = conn.execute(
            "INSERT INTO places (name, category, latitude, longitude) "
            "VALUES ('Bureau', 'work', 1, 1)"
        ).lastrowid
        for title, timestamp in (
            ("before", "2026-07-09 21:59:59"),
            ("start", "2026-07-09 22:00:00"),
            ("end-minus-one", "2026-07-10 21:59:59"),
            ("end", "2026-07-10 22:00:00"),
        ):
            conn.execute(
                "INSERT INTO tasks (title, status, completed_at) VALUES (?, 'done', ?)",
                (title, timestamp),
            )
            conn.execute(
                "INSERT INTO visits (place_id, arrived_at) VALUES (?, ?)",
                (place_id, timestamp),
            )

    score = _day_summary_for_scoring("2026-07-10")
    facts = _day_facts("2026-07-10")
    timeline = build_day_timeline("2026-07-10")

    assert score["tasks_done"] == 2
    assert facts["tasks_done"] == ["start", "end-minus-one"]
    assert facts["visits"] == ["Bureau", "Bureau"]
    assert timeline["summary"]["tasks_done"] == 2
    assert timeline["summary"]["visits"] == 2
    assert [
        event["time"]
        for event in timeline["timeline"]
        if event["type"] == "task_done"
    ] == ["00:00", "23:59"]

    from scripts import rituals

    original_today = rituals._today
    rituals._today = lambda: "2026-07-10"
    try:
        assert len(_day_snapshot()["tasks_done"]) == 2
    finally:
        rituals._today = original_today


def test_activity_timestamp_writers_store_canonical_utc(paris_db: Path) -> None:
    from database import get_db
    from database.location_helpers import create_trip, end_visit, start_visit

    zone = ZoneInfo("Europe/Paris")
    local_start = datetime(2026, 7, 10, 0, 30, tzinfo=zone)
    local_end = datetime(2026, 7, 10, 1, 45, tzinfo=zone)
    with get_db() as conn:
        place_id = conn.execute(
            "INSERT INTO places (name, category, latitude, longitude) "
            "VALUES ('Maison', 'home', 1, 1)"
        ).lastrowid

    visit_id = start_visit(place_id, local_start)
    end_visit(visit_id, local_end)
    trip_id = create_trip(place_id, None, local_start, local_end)

    with get_db() as conn:
        visit = conn.execute(
            "SELECT arrived_at, departed_at, day_of_week, duration_min "
            "FROM visits WHERE id = ?",
            (visit_id,),
        ).fetchone()
        trip = conn.execute(
            "SELECT started_at, ended_at FROM trips WHERE id = ?", (trip_id,)
        ).fetchone()

    assert tuple(visit) == (
        "2026-07-09 22:30:00",
        "2026-07-09 23:45:00",
        4,
        75.0,
    )
    assert tuple(trip) == ("2026-07-09 22:30:00", "2026-07-09 23:45:00")


def test_legacy_local_timestamp_migration_is_idempotent(paris_db: Path) -> None:
    from database import get_db
    from database.migrations import _migrate_local_activity_timestamps_to_utc

    with get_db() as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key = 'timestamp_storage_utc_v1'"
        )
        place_id = conn.execute(
            "INSERT INTO places (name, category, latitude, longitude, last_visit) "
            "VALUES ('Legacy', 'other', 1, 1, '2026-07-10 00:30:00')"
        ).lastrowid
        visit_id = conn.execute(
            "INSERT INTO visits (place_id, arrived_at) VALUES (?, '2026-07-10 00:30:00')",
            (place_id,),
        ).lastrowid
        _migrate_local_activity_timestamps_to_utc(conn)
        first = conn.execute(
            "SELECT arrived_at FROM visits WHERE id = ?", (visit_id,)
        ).fetchone()[0]
        _migrate_local_activity_timestamps_to_utc(conn)
        second = conn.execute(
            "SELECT arrived_at FROM visits WHERE id = ?", (visit_id,)
        ).fetchone()[0]

    assert first == "2026-07-09 22:30:00"
    assert second == first


def test_today_location_and_presence_use_configured_local_day(
    paris_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database.location_helpers as locations
    from database import get_db
    from scripts import presence

    fixed = datetime(2026, 7, 10, 0, 30, tzinfo=ZoneInfo("Europe/Paris"))
    monkeypatch.setattr(locations, "local_datetime", lambda: fixed)
    monkeypatch.setattr(presence, "local_datetime", lambda: fixed)
    with get_db() as conn:
        place_id = conn.execute(
            "INSERT INTO places (name, category, latitude, longitude) "
            "VALUES ('Bureau', 'work', 1, 1)"
        ).lastrowid
        conn.execute(
            "INSERT INTO visits (place_id, arrived_at) VALUES (?, '2026-07-09 22:15:00')",
            (place_id,),
        )
        conn.execute(
            "INSERT INTO visits (place_id, arrived_at) VALUES (?, '2026-07-09 21:59:59')",
            (place_id,),
        )
        conn.execute(
            "INSERT INTO trips (started_at, ended_at) "
            "VALUES ('2026-07-09 21:30:00', '2026-07-09 22:15:00')"
        )
        conn.execute(
            "INSERT INTO presence_sessions (arrived_at) VALUES ('2026-07-09 22:20:00')"
        )

    assert len(locations.get_today_visits()) == 1
    assert len(locations.get_trips_for_today()) == 1
    assert len(presence.get_today_sessions()) == 1


def test_presence_epoch_is_persisted_as_utc(paris_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from database import get_db
    from scripts.presence import PresenceDetector

    monkeypatch.setattr("config.PRESENCE_ENABLED", True)
    instant = datetime(2026, 7, 9, 22, 30, tzinfo=timezone.utc).timestamp()
    detector = PresenceDetector()
    assert detector.on_sound(instant) == "arrived"
    with get_db() as conn:
        stored = conn.execute(
            "SELECT arrived_at FROM presence_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert stored == "2026-07-09 22:30:00"


def test_week_windows_use_local_calendar_boundaries(
    paris_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database.rituals as ritual_store
    from database import get_db
    from scripts import rituals

    fixed = datetime(2026, 7, 10, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
    monkeypatch.setattr(ritual_store, "local_datetime", lambda: fixed)
    monkeypatch.setattr(rituals, "local_datetime", lambda: fixed)
    monkeypatch.setattr(
        ritual_store,
        "get_daily_activity_stats",
        lambda _days: [
            {
                "msg_count": 0,
                "voice_count": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
            }
            for _ in range(14)
        ],
    )
    with get_db() as conn:
        for title, timestamp in (
            ("outside-previous", "2026-06-26 21:59:59"),
            ("previous-start", "2026-06-26 22:00:00"),
            ("previous-end", "2026-07-03 21:59:59"),
            ("current-start", "2026-07-03 22:00:00"),
            ("current-end", "2026-07-10 22:00:00"),
        ):
            conn.execute(
                "INSERT INTO tasks (title, status, completed_at) VALUES (?, 'done', ?)",
                (title, timestamp),
            )

    comparison = ritual_store.get_week_comparison()
    assert comparison["last_week"]["tasks_done"] == 2
    assert comparison["this_week"]["tasks_done"] == 1
    assert rituals.compute_productivity_score()["done_7d"] == 1
    assert rituals._week_snapshot()["tasks_done"] == ["current-start"]


def test_local_calendar_fields_do_not_depend_on_sqlite_utc_today(
    paris_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database.screen_daemon as screen
    from database import get_db, people

    fixed = datetime(2026, 7, 10, 0, 30, tzinfo=ZoneInfo("Europe/Paris"))
    monkeypatch.setattr(people, "local_datetime", lambda: fixed)
    monkeypatch.setattr(screen, "local_datetime", lambda: fixed)
    with get_db() as conn:
        context_id = conn.execute(
            "INSERT INTO life_context (context_type, description) "
            "VALUES ('project', 'test')"
        ).lastrowid
        conn.execute(
            "INSERT INTO app_usage (device, app, date, duration_seconds) "
            "VALUES ('mac', 'old', '2026-07-03', 1)"
        )
        conn.execute(
            "INSERT INTO app_usage (device, app, date, duration_seconds) "
            "VALUES ('mac', 'inside', '2026-07-04', 1)"
        )

    assert people.close_life_context(context_id) is True
    screen.upsert_app_usage("mac", "today", 30)
    assert {row["app"] for row in screen.get_app_usage_range(7)} == {
        "inside",
        "today",
    }
    with get_db() as conn:
        assert conn.execute(
            "SELECT period_end FROM life_context WHERE id = ?", (context_id,)
        ).fetchone()[0] == "2026-07-10"


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


def test_runtime_queries_never_bucket_utc_timestamps_with_sqlite_date() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for directory in ("database", "scripts", "api"):
        for path in (root / directory).rglob("*.py"):
            source = path.read_text(encoding="utf-8").lower().replace(" ", "")
            for column in (
                "created_at",
                "completed_at",
                "arrived_at",
                "departed_at",
                "started_at",
                "ended_at",
            ):
                if f"date({column})" in source or f"date(v.{column})" in source:
                    offenders.append(str(path.relative_to(root)))
                    break
    assert offenders == []


def test_sqlite_utc_parser_accepts_aware_values(
    paris_db: Path,
) -> None:
    from database.time_buckets import sqlite_utc_to_local

    converted = sqlite_utc_to_local(datetime.fromisoformat("2026-10-25T00:30:00+00:00"))
    assert converted.isoformat() == "2026-10-25T02:30:00+02:00"
