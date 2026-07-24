"""Tests des statistiques d'activité quotidienne (/api/stats/weekly)."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _insert_message(
    conn,
    conv_id: int,
    created_at: str,
    tokens_in: int = 10,
    tokens_out: int = 20,
    cost: float = 0.001,
    role: str = "assistant",
) -> None:
    conn.execute(
        """INSERT INTO messages (conversation_id, role, content, tokens_in, tokens_out, cost, created_at)
           VALUES (?, ?, 'x', ?, ?, ?, ?)""",
        (conv_id, role, tokens_in, tokens_out, cost, created_at),
    )


def test_daily_stats_fills_missing_days(tmp_db):
    from database import get_daily_activity_stats

    stats = get_daily_activity_stats(7)
    assert len(stats) == 7
    assert all(d["msg_count"] == 0 for d in stats)
    # série continue, plus ancien en premier
    dates = [d["date"] for d in stats]
    assert dates == sorted(dates)
    assert dates[-1] == datetime.now().date().isoformat()


def test_daily_stats_aggregates_and_splits_voice(tmp_db):
    from database import get_daily_activity_stats, get_db

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        conn.execute("INSERT INTO conversations (id, agent) VALUES (2, 'voice')")
        _insert_message(
            conn,
            1,
            f"{today} 10:00:00",
            tokens_in=100,
            tokens_out=50,
            role="user",
        )
        _insert_message(conn, 2, f"{today} 11:00:00", tokens_in=5, tokens_out=5)
        _insert_message(conn, 1, f"{yesterday} 09:00:00", role="user")

    stats = get_daily_activity_stats(7)
    last, prev = stats[-1], stats[-2]
    assert last["date"] == today.isoformat()
    assert last["msg_count"] == 2
    assert last["voice_count"] == 1
    assert last["turn_count"] == 1
    assert last["tokens_in"] == 105
    assert last["tokens_out"] == 55
    assert prev["msg_count"] == 1
    assert prev["voice_count"] == 0
    assert prev["turn_count"] == 1


def test_daily_stats_clamps_days(tmp_db):
    from database import get_daily_activity_stats

    assert len(get_daily_activity_stats(0)) == 1
    assert len(get_daily_activity_stats(500)) == 90


@pytest.mark.parametrize(
    ("local_day", "start_utc", "end_utc"),
    [
        (date(2026, 3, 29), "2026-03-28 23:00:00", "2026-03-29 22:00:00"),
        (date(2026, 10, 25), "2026-10-24 22:00:00", "2026-10-25 23:00:00"),
    ],
)
def test_daily_stats_respect_paris_dst_boundaries(
    tmp_db,
    monkeypatch: pytest.MonkeyPatch,
    local_day: date,
    start_utc: str,
    end_utc: str,
):
    from database import get_cost_summary, get_daily_activity_stats, get_db, get_usage_stats

    monkeypatch.setattr("config.TIMEZONE", "Europe/Paris")
    start = datetime.strptime(start_utc, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_utc, "%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        for timestamp in (
            start - timedelta(seconds=1),
            start,
            end - timedelta(seconds=1),
            end,
        ):
            _insert_message(
                conn,
                1,
                timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                role="user",
            )

    now = datetime.combine(local_day, time(12), tzinfo=ZoneInfo("Europe/Paris"))
    daily = get_daily_activity_stats(1, now=now)
    usage = get_usage_stats(now=now)
    costs = get_cost_summary(now=now)

    assert daily[0]["date"] == local_day.isoformat()
    assert daily[0]["msg_count"] == 2
    assert daily[0]["turn_count"] == 2
    assert usage["msg_count"] == 2
    assert usage["turn_count"] == 2
    assert costs["today"]["msg_count"] == 2


def test_stats_weekly_endpoint(tmp_db):
    """L'endpoint calcule variations jour/jour et totaux."""
    from datetime import date

    import main
    from database import get_db
    from fastapi.testclient import TestClient

    today = date.today()
    yesterday = today - timedelta(days=1)
    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        _insert_message(
            conn,
            1,
            f"{yesterday} 09:00:00",
            tokens_in=10,
            tokens_out=10,
            role="user",
        )
        _insert_message(
            conn,
            1,
            f"{today} 09:00:00",
            tokens_in=20,
            tokens_out=20,
            role="user",
        )
        _insert_message(conn, 1, f"{today} 09:00:01", tokens_in=20, tokens_out=20)
        _insert_message(
            conn,
            1,
            f"{today} 10:00:00",
            tokens_in=20,
            tokens_out=20,
            role="user",
        )
        _insert_message(conn, 1, f"{today} 10:00:01", tokens_in=20, tokens_out=20)

    from tests.conftest import authenticate

    with TestClient(main.app) as client:
        authenticate(client)
        r = client.get("/api/stats/weekly?days=7")
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 7
    assert body["totals"]["msg_count"] == 5
    assert body["totals"]["turn_count"] == 3
    # 4 messages aujourd'hui vs 1 hier → +300 %
    assert body["change"]["messages_pct"] == 300.0
    # 2 messages utilisateur aujourd'hui vs 1 hier → +100 % de tours.
    assert body["change"]["turns_pct"] == 100.0
    assert body["change"]["interactions_pct"] == 100.0
