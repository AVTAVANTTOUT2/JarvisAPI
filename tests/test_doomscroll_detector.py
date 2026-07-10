"""Tests : détecteur de doomscrolling (heuristique sur app_usage)."""

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
    monkeypatch.setattr("config.DOOMSCROLL_APPS", "instagram,tiktok,twitter,x,reddit")
    monkeypatch.setattr("config.DOOMSCROLL_DAILY_MINUTES", 90)
    from database import init_db

    init_db()
    return db_path


def test_flags_day_over_threshold():
    from scripts.doomscroll_detector import analyze_doomscroll

    rows = [
        {"date": "2026-07-10", "app": "Instagram", "duration_seconds": 3600},  # 60 min
        {"date": "2026-07-10", "app": "TikTok", "duration_seconds": 2400},     # 40 min -> 100 total
    ]
    results = analyze_doomscroll(rows, daily_minutes_threshold=90)
    assert len(results) == 1
    assert results[0]["date"] == "2026-07-10"
    assert results[0]["total_minutes"] == 100.0


def test_ignores_day_under_threshold():
    from scripts.doomscroll_detector import analyze_doomscroll

    rows = [{"date": "2026-07-10", "app": "Instagram", "duration_seconds": 1800}]  # 30 min
    results = analyze_doomscroll(rows, daily_minutes_threshold=90)
    assert results == []


def test_ignores_non_doomscroll_apps():
    from scripts.doomscroll_detector import analyze_doomscroll

    rows = [{"date": "2026-07-10", "app": "Visual Studio Code", "duration_seconds": 36000}]
    results = analyze_doomscroll(rows, daily_minutes_threshold=90)
    assert results == []


def test_app_name_matching_is_case_insensitive_substring():
    from scripts.doomscroll_detector import analyze_doomscroll

    rows = [{"date": "2026-07-10", "app": "TikTok - Make Your Day", "duration_seconds": 6000}]
    results = analyze_doomscroll(rows, daily_minutes_threshold=90)
    assert len(results) == 1


def test_multiple_days_sorted_most_recent_first():
    from scripts.doomscroll_detector import analyze_doomscroll

    rows = [
        {"date": "2026-07-08", "app": "Reddit", "duration_seconds": 6000},
        {"date": "2026-07-10", "app": "Reddit", "duration_seconds": 6000},
        {"date": "2026-07-09", "app": "Reddit", "duration_seconds": 6000},
    ]
    results = analyze_doomscroll(rows, daily_minutes_threshold=90)
    assert [r["date"] for r in results] == ["2026-07-10", "2026-07-09", "2026-07-08"]


def test_apps_breakdown_sorted_by_minutes_desc():
    from scripts.doomscroll_detector import analyze_doomscroll

    rows = [
        {"date": "2026-07-10", "app": "Instagram", "duration_seconds": 1200},
        {"date": "2026-07-10", "app": "TikTok", "duration_seconds": 6000},
    ]
    results = analyze_doomscroll(rows, daily_minutes_threshold=90)
    assert results[0]["apps"][0]["app"] == "TikTok"


def test_detect_doomscrolling_uses_real_db(tmp_db):
    from database import upsert_app_usage
    from scripts.doomscroll_detector import detect_doomscrolling

    upsert_app_usage("mac_mini", "Instagram", 3600)
    upsert_app_usage("mac_mini", "TikTok", 3600)

    results = detect_doomscrolling(days=7)
    assert len(results) == 1
    assert results[0]["total_minutes"] == 120.0


def test_check_and_notify_today_creates_notification(tmp_db):
    from database import get_unread_notifications, upsert_app_usage
    from scripts.doomscroll_detector import check_and_notify_today

    upsert_app_usage("mac_mini", "Instagram", 6000)

    result = check_and_notify_today()
    assert result is not None
    notifs = get_unread_notifications(10)
    assert any(n["title"] == "Doomscrolling" for n in notifs)


def test_check_and_notify_today_no_duplicate_same_day(tmp_db):
    from database import get_unread_notifications, upsert_app_usage
    from scripts.doomscroll_detector import check_and_notify_today

    upsert_app_usage("mac_mini", "Instagram", 6000)
    check_and_notify_today()
    check_and_notify_today()

    notifs = [n for n in get_unread_notifications(10) if n["title"] == "Doomscrolling"]
    assert len(notifs) == 1


def test_check_and_notify_today_returns_none_when_under_threshold(tmp_db):
    from scripts.doomscroll_detector import check_and_notify_today

    assert check_and_notify_today() is None
