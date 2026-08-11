"""Tests des buckets persistants, tendances et règles de rétention métriques."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "metric-history.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def test_metric_samples_are_bucketed_and_weighted(tmp_db):
    from database import get_metric_history, record_metric_samples

    now = datetime.now(timezone.utc).replace(second=10, microsecond=0)
    record_metric_samples(
        {"health.score": (50, "percent")},
        recorded_at=now - timedelta(minutes=10),
    )
    record_metric_samples({"health.score": (100, "percent")}, recorded_at=now)
    record_metric_samples({"health.score": (80, "percent")}, recorded_at=now)

    history = get_metric_history(1)
    score = next(series for series in history["series"] if series["metric"] == "health.score")

    assert len(score["points"]) == 2
    assert score["points"][-1]["value"] == 90
    assert score["points"][-1]["last_value"] == 80
    assert score["points"][-1]["samples"] == 2
    assert score["summary"]["average"] == pytest.approx(76.67, abs=0.01)
    assert score["summary"]["trend_pct"] == 60


def test_health_snapshot_exposes_component_latency_and_api_contract(tmp_db):
    from api.health_support import api_metrics_history
    from database import record_health_snapshot

    record_health_snapshot(
        {
            "status": "degraded",
            "duration_ms": 12.5,
            "components": [
                {
                    "name": "database",
                    "state": "healthy",
                    "details": {"latency_ms": 1.25},
                }
            ],
        }
    )

    response = asyncio.run(api_metrics_history(hours=24))
    metrics = {series["metric"] for series in response["series"]}
    assert metrics == {
        "health.score",
        "health.duration_ms",
        "health.database.score",
        "health.database.latency_ms",
    }
    assert response["bucket_seconds"] == 300
    assert response["retention_days"] >= 7
    assert all(series["summary"]["trend_pct"] is None for series in response["series"])


def test_database_maintenance_applies_metric_retention(tmp_db, monkeypatch):
    from database import record_metric_samples
    from scripts.db_maintenance import run_maintenance

    now = datetime.now(timezone.utc)
    record_metric_samples(
        {"health.score": (25, "percent")},
        recorded_at=now - timedelta(days=10),
    )
    record_metric_samples({"health.score": (100, "percent")}, recorded_at=now)
    monkeypatch.setattr("config.RETENTION_SCREEN_DAYS", 0)
    monkeypatch.setattr("config.RETENTION_LOCATION_DAYS", 0)
    monkeypatch.setattr("config.RETENTION_LLM_LOGS_DAYS", 0)
    monkeypatch.setattr("config.RETENTION_NOTIF_READ_DAYS", 0)
    monkeypatch.setattr("config.RETENTION_METRICS_DAYS", 7)

    report = run_maintenance()

    assert report["purged"]["metric_samples"] == 1
