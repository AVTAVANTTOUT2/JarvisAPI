"""Tests : score de cohérence promesses/actions (heuristique déterministe)."""

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


def test_no_resolved_commitments_returns_none_score():
    from scripts.commitment_consistency import compute_consistency_score

    result = compute_consistency_score([{"status": "open", "created_at": datetime.now().isoformat()}])
    assert result["score"] is None
    assert "assez" in result["explanation"]


def test_all_kept_gives_perfect_score():
    from scripts.commitment_consistency import compute_consistency_score

    commitments = [{"status": "kept", "created_at": datetime.now().isoformat()} for _ in range(5)]
    result = compute_consistency_score(commitments)
    assert result["score"] == 100


def test_all_dropped_gives_zero_score():
    from scripts.commitment_consistency import compute_consistency_score

    commitments = [{"status": "dropped", "created_at": datetime.now().isoformat()} for _ in range(3)]
    result = compute_consistency_score(commitments)
    assert result["score"] == 0


def test_mixed_kept_dropped_ratio():
    from scripts.commitment_consistency import compute_consistency_score

    commitments = (
        [{"status": "kept", "created_at": datetime.now().isoformat()} for _ in range(3)]
        + [{"status": "dropped", "created_at": datetime.now().isoformat()} for _ in range(1)]
    )
    result = compute_consistency_score(commitments)
    assert result["score"] == 75


def test_overdue_open_commitments_penalize_score():
    from scripts.commitment_consistency import compute_consistency_score

    now = datetime(2026, 7, 10)
    old_open = (now - timedelta(days=10)).isoformat()
    commitments = (
        [{"status": "kept", "created_at": now.isoformat()} for _ in range(4)]
        + [{"status": "open", "created_at": old_open}]
    )
    result = compute_consistency_score(commitments, now=now, overdue_days=3)
    assert result["score"] < 100
    assert result["overdue_open"] == 1


def test_recent_open_commitment_not_counted_as_overdue():
    from scripts.commitment_consistency import compute_consistency_score

    now = datetime(2026, 7, 10)
    recent_open = (now - timedelta(days=1)).isoformat()
    commitments = (
        [{"status": "kept", "created_at": now.isoformat()} for _ in range(2)]
        + [{"status": "open", "created_at": recent_open}]
    )
    result = compute_consistency_score(commitments, now=now, overdue_days=3)
    assert result["overdue_open"] == 0
    assert result["score"] == 100


def test_score_clamped_between_0_and_100():
    from scripts.commitment_consistency import compute_consistency_score

    now = datetime(2026, 7, 10)
    old_open = (now - timedelta(days=100)).isoformat()
    commitments = (
        [{"status": "kept", "created_at": now.isoformat()}]
        + [{"status": "open", "created_at": old_open} for _ in range(20)]
    )
    result = compute_consistency_score(commitments, now=now, overdue_days=3)
    assert 0 <= result["score"] <= 100


def test_get_consistency_score_uses_real_db(tmp_db):
    from database import add_commitment, get_db, update_commitment_status
    from scripts.commitment_consistency import get_consistency_score

    c1 = add_commitment("Envoyer le devis")
    c2 = add_commitment("Rappeler le client")
    update_commitment_status(c1, "kept")
    update_commitment_status(c2, "dropped")

    result = get_consistency_score(days=90)
    assert result["score"] == 50
    assert result["kept"] == 1
    assert result["dropped"] == 1
