"""Tests détection de régression de performance + rollback DevAgent."""

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
    from database import init_db

    init_db()
    return db_path


def test_no_baseline_yet_never_flags(tmp_db):
    from scripts.perf_regression import check_regression

    assert check_regression("jarvis", 100_000) is None  # aucun historique


def test_baseline_requires_at_least_two_points(tmp_db):
    from database import record_perf_benchmark
    from scripts.perf_regression import check_regression

    record_perf_benchmark("jarvis", "sha1", 1000)
    assert check_regression("jarvis", 5000) is None  # un seul point = pas assez


def test_regression_detected_above_threshold(tmp_db, monkeypatch):
    from database import record_perf_benchmark
    from scripts.perf_regression import check_regression

    monkeypatch.setattr("config.PERF_REGRESSION_THRESHOLD_PCT", 40)
    for d in (1000, 1050, 950, 1020):
        record_perf_benchmark("jarvis", "sha", d)
    # baseline médiane ~1010 ; 1450 = +43% > seuil 40%
    report = check_regression("jarvis", 1450)
    assert report is not None
    assert report["pct"] > 40
    assert report["scope"] == "jarvis"


def test_no_regression_within_threshold(tmp_db, monkeypatch):
    from database import record_perf_benchmark
    from scripts.perf_regression import check_regression

    monkeypatch.setattr("config.PERF_REGRESSION_THRESHOLD_PCT", 40)
    for d in (1000, 1000, 1000):
        record_perf_benchmark("jarvis", "sha", d)
    assert check_regression("jarvis", 1200) is None  # +20%, sous le seuil


def test_record_and_check_does_not_pollute_own_baseline(tmp_db):
    from database import get_perf_history
    from scripts.perf_regression import record_and_check

    from database import record_perf_benchmark
    for d in (100, 100, 100):
        record_perf_benchmark("jarvis", "sha", d)

    report = record_and_check("jarvis", "sha-new", 100_000)
    assert report is not None  # régression massive détectée
    history = get_perf_history("jarvis", limit=10)
    assert len(history) == 4  # le nouveau point EST enregistré après coup
    assert history[0]["duration_ms"] == 100_000


@pytest.mark.asyncio
async def test_guard_devagent_rolls_back_on_regression(tmp_db, tmp_path, monkeypatch):
    from agents.devagent.executor import git_commit, git_init, run_isolated
    from database import get_unread_notifications, record_perf_benchmark
    from scripts.perf_regression import guard_devagent_iteration

    project = tmp_path / "proj"
    project.mkdir()
    git_init(project)
    (project / "a.txt").write_text("v1", encoding="utf-8")
    git_commit(project, "commit régressif")

    monkeypatch.setattr("config.PERF_REGRESSION_THRESHOLD_PCT", 30)
    for d in (500, 520, 480):
        record_perf_benchmark("proj-slug", "old-sha", d)

    log_before = run_isolated(["git", "log", "--oneline"], cwd=project)["stdout"]
    assert "commit régressif" in log_before

    result = await guard_devagent_iteration(project, "proj-slug", "new-sha", duration_ms=2000)
    assert result["rolled_back"] is True

    log_after = run_isolated(["git", "log", "--oneline"], cwd=project)["stdout"]
    assert "Revert" in log_after or "revert" in log_after.lower()
    notifs = [n for n in get_unread_notifications(10) if n["title"].startswith("Rollback perf")]
    assert len(notifs) == 1


@pytest.mark.asyncio
async def test_guard_devagent_no_rollback_when_healthy(tmp_db, tmp_path, monkeypatch):
    from agents.devagent.executor import git_init
    from database import get_perf_history
    from scripts.perf_regression import guard_devagent_iteration

    project = tmp_path / "proj2"
    project.mkdir()
    git_init(project)

    result = await guard_devagent_iteration(project, "proj2-slug", "sha", duration_ms=100)
    assert result == {"rolled_back": False}
    assert len(get_perf_history("proj2-slug")) == 1
