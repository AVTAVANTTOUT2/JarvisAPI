"""Tests des statistiques d'activité quotidienne (/api/stats/weekly)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
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


def _insert_message(conn, conv_id: int, created_at: str, tokens_in: int = 10,
                    tokens_out: int = 20, cost: float = 0.001) -> None:
    conn.execute(
        """INSERT INTO messages (conversation_id, role, content, tokens_in, tokens_out, cost, created_at)
           VALUES (?, 'assistant', 'x', ?, ?, ?, ?)""",
        (conv_id, tokens_in, tokens_out, cost, created_at),
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
        _insert_message(conn, 1, f"{today} 10:00:00", tokens_in=100, tokens_out=50)
        _insert_message(conn, 2, f"{today} 11:00:00", tokens_in=5, tokens_out=5)
        _insert_message(conn, 1, f"{yesterday} 09:00:00")

    stats = get_daily_activity_stats(7)
    last, prev = stats[-1], stats[-2]
    assert last["date"] == today.isoformat()
    assert last["msg_count"] == 2
    assert last["voice_count"] == 1
    assert last["tokens_in"] == 105
    assert last["tokens_out"] == 55
    assert prev["msg_count"] == 1
    assert prev["voice_count"] == 0


def test_daily_stats_clamps_days(tmp_db):
    from database import get_daily_activity_stats

    assert len(get_daily_activity_stats(0)) == 1
    assert len(get_daily_activity_stats(500)) == 90


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
        _insert_message(conn, 1, f"{yesterday} 09:00:00", tokens_in=10, tokens_out=10)
        _insert_message(conn, 1, f"{today} 09:00:00", tokens_in=20, tokens_out=20)
        _insert_message(conn, 1, f"{today} 10:00:00", tokens_in=20, tokens_out=20)

    from tests.conftest import authenticate

    with TestClient(main.app) as client:
        authenticate(client)
        r = client.get("/api/stats/weekly?days=7")
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 7
    assert body["totals"]["msg_count"] == 3
    # 2 messages aujourd'hui vs 1 hier → +100 %
    assert body["change"]["messages_pct"] == 100.0
    # 80 tokens aujourd'hui vs 20 hier → +300 %
    assert body["change"]["interactions_pct"] == 300.0
