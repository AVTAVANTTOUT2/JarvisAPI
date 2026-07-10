"""Tests : lieux favoris + détection d'opportunités manquées."""

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
    monkeypatch.setattr("config.FAVORITE_PLACE_MIN_VISITS", 5)
    monkeypatch.setattr("config.OPPORTUNITY_MIN_DAYS_NAMED", 30)
    from database import init_db

    init_db()
    return db_path


def _make_place(name: str, category: str, visit_count: int, last_visit: str | None, avg_duration=45.0):
    from database import get_db

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO places (name, category, latitude, longitude, visit_count, avg_duration_min, last_visit)
               VALUES (?, ?, 50.6, 3.0, ?, ?, ?)""",
            (name, category, visit_count, avg_duration, last_visit),
        )
        return cur.lastrowid


def test_favorite_places_filters_by_min_visits(tmp_db):
    from scripts.favorite_places import get_favorite_places

    _make_place("Salle de sport", "gym", 12, "2026-07-01T18:00:00")
    _make_place("Boulangerie", "shop", 2, "2026-07-09T08:00:00")  # sous le seuil

    favorites = get_favorite_places()
    names = [f["name"] for f in favorites]
    assert "Salle de sport" in names
    assert "Boulangerie" not in names


def test_favorite_places_sorted_by_visit_count_desc(tmp_db):
    from scripts.favorite_places import get_favorite_places

    _make_place("Resto A", "restaurant", 8, "2026-07-01T12:00:00")
    _make_place("Resto B", "restaurant", 20, "2026-07-02T12:00:00")

    favorites = get_favorite_places()
    assert favorites[0]["name"] == "Resto B"
    assert favorites[1]["name"] == "Resto A"


def test_detect_missed_opportunity_flags_stale_favorite(tmp_db):
    from scripts.favorite_places import detect_missed_opportunities

    now = datetime(2026, 7, 10)
    stale_date = (now - timedelta(days=45)).isoformat(timespec="seconds")
    _make_place("Escalade Loopino", "leisure", 15, stale_date)

    results = detect_missed_opportunities(now=now)
    assert len(results) == 1
    assert results[0]["name"] == "Escalade Loopino"
    assert results[0]["days_since_last_visit"] == 45
    assert "Escalade Loopino" in results[0]["message"]


def test_detect_missed_opportunity_ignores_recent_favorite(tmp_db):
    from scripts.favorite_places import detect_missed_opportunities

    now = datetime(2026, 7, 10)
    recent_date = (now - timedelta(days=5)).isoformat(timespec="seconds")
    _make_place("Piscine", "leisure", 10, recent_date)

    results = detect_missed_opportunities(now=now)
    assert results == []


def test_detect_missed_opportunity_ignores_home_category(tmp_db):
    from scripts.favorite_places import detect_missed_opportunities

    now = datetime(2026, 7, 10)
    stale_date = (now - timedelta(days=90)).isoformat(timespec="seconds")
    _make_place("Maison", "home", 500, stale_date)

    results = detect_missed_opportunities(now=now)
    assert results == []


def test_detect_missed_opportunity_ignores_below_min_visits(tmp_db):
    from scripts.favorite_places import detect_missed_opportunities

    now = datetime(2026, 7, 10)
    stale_date = (now - timedelta(days=60)).isoformat(timespec="seconds")
    _make_place("Café ponctuel", "restaurant", 2, stale_date)

    results = detect_missed_opportunities(now=now)
    assert results == []


def test_detect_missed_opportunity_sorted_by_days_since_desc(tmp_db):
    from scripts.favorite_places import detect_missed_opportunities

    now = datetime(2026, 7, 10)
    _make_place("A", "leisure", 10, (now - timedelta(days=35)).isoformat(timespec="seconds"))
    _make_place("B", "leisure", 10, (now - timedelta(days=80)).isoformat(timespec="seconds"))

    results = detect_missed_opportunities(now=now)
    assert [r["name"] for r in results] == ["B", "A"]


def test_check_and_notify_weekly_creates_notification(tmp_db):
    from database import get_unread_notifications
    from scripts.favorite_places import check_and_notify_weekly

    now = datetime.now()
    stale = (now - timedelta(days=45)).isoformat(timespec="seconds")
    _make_place("Escalade Loopino", "leisure", 15, stale)

    result = check_and_notify_weekly()
    assert len(result) == 1
    notifs = get_unread_notifications(10)
    assert any("Lieux délaissés" in n["title"] for n in notifs)


def test_check_and_notify_weekly_no_duplicate_same_week(tmp_db):
    from database import get_unread_notifications
    from scripts.favorite_places import check_and_notify_weekly

    now = datetime.now()
    stale = (now - timedelta(days=45)).isoformat(timespec="seconds")
    _make_place("Escalade Loopino", "leisure", 15, stale)

    check_and_notify_weekly()
    check_and_notify_weekly()

    notifs = [n for n in get_unread_notifications(10) if "Lieux délaissés" in n["title"]]
    assert len(notifs) == 1


def test_check_and_notify_weekly_empty_when_no_opportunities(tmp_db):
    from scripts.favorite_places import check_and_notify_weekly

    assert check_and_notify_weekly() == []
