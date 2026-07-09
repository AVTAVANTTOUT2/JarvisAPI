"""Tests fiabilité — sauvegardes SQLite, rétention, budget LLM, heures calmes."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.BACKUP_DIR", str(tmp_path / "backups"))
    from database import init_db

    init_db()
    return db_path


# ── Sauvegardes ──────────────────────────────────────────────

def test_backup_creates_valid_snapshot(tmp_db, tmp_path):
    from scripts.db_maintenance import list_backups, run_backup

    report = run_backup()
    assert report["ok"] is True
    dest = Path(report["path"])
    assert dest.exists() and report["size_bytes"] > 0

    # le snapshot est une base SQLite valide contenant les tables JARVIS
    conn = sqlite3.connect(dest)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "messages" in tables and "conversations" in tables

    backups = list_backups()
    assert backups and backups[0]["name"] == dest.name


def test_backup_rotation_keeps_most_recent(tmp_db, monkeypatch):
    import os

    from scripts.db_maintenance import _rotate_backups, list_backups, run_backup

    # 4 sauvegardes sans rotation (keep élevé), mtimes croissants explicites
    monkeypatch.setattr("config.BACKUP_KEEP", 100)
    paths = []
    for i in range(4):
        r = run_backup()
        assert r["ok"]
        os.utime(r["path"], (1_000_000 + i, 1_000_000 + i))
        paths.append(r["path"])

    removed = _rotate_backups(Path(config.BACKUP_DIR), keep=2)
    assert len(removed) == 2
    remaining = {b["name"] for b in list_backups()}
    assert remaining == {Path(paths[-1]).name, Path(paths[-2]).name}
    assert Path(paths[0]).name in removed


def test_backup_missing_db_fails_cleanly(tmp_db, monkeypatch, tmp_path):
    from scripts.db_maintenance import run_backup

    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "absente.db"))
    report = run_backup()
    assert report["ok"] is False and "introuvable" in report["error"]


# ── Rétention / maintenance ──────────────────────────────────

def _insert_aged_rows(conn) -> None:
    conn.execute(
        "INSERT INTO screen_activity (device, app, created_at) VALUES ('mac', 'old', datetime('now', '-40 days'))")
    conn.execute(
        "INSERT INTO screen_activity (device, app, created_at) VALUES ('mac', 'new', datetime('now', '-1 day'))")
    conn.execute(
        "INSERT INTO llm_action_logs (agent, action_type, created_at) VALUES ('info', 'x', datetime('now', '-120 days'))")
    conn.execute(
        "INSERT INTO llm_action_logs (agent, action_type, created_at) VALUES ('info', 'y', datetime('now', '-5 days'))")
    conn.execute(
        "INSERT INTO notifications (source, title, read, created_at) VALUES ('email', 'lue ancienne', 1, datetime('now', '-90 days'))")
    conn.execute(
        "INSERT INTO notifications (source, title, read, created_at) VALUES ('email', 'NON lue ancienne', 0, datetime('now', '-90 days'))")


def test_maintenance_purges_by_retention(tmp_db):
    from database import get_db
    from scripts.db_maintenance import run_maintenance

    with get_db() as conn:
        _insert_aged_rows(conn)

    report = run_maintenance()
    assert report["ok"]
    assert report["purged"]["screen_activity"] == 1
    assert report["purged"]["llm_action_logs"] == 1
    assert report["purged"]["notifications_read"] == 1

    with get_db() as conn:
        apps = [r[0] for r in conn.execute("SELECT app FROM screen_activity")]
        titles = [r[0] for r in conn.execute("SELECT title FROM notifications")]
    assert apps == ["new"]
    # une notification non lue n'est JAMAIS purgée, même ancienne
    assert titles == ["NON lue ancienne"]


def test_maintenance_zero_days_keeps_everything(tmp_db, monkeypatch):
    from database import get_db
    from scripts.db_maintenance import run_maintenance

    monkeypatch.setattr("config.RETENTION_SCREEN_DAYS", 0)
    monkeypatch.setattr("config.RETENTION_LLM_LOGS_DAYS", 0)
    monkeypatch.setattr("config.RETENTION_NOTIF_READ_DAYS", 0)
    with get_db() as conn:
        _insert_aged_rows(conn)

    report = run_maintenance()
    assert "screen_activity" not in report["purged"]
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM screen_activity").fetchone()[0]
    assert n == 2


# ── Coûts / budget ───────────────────────────────────────────

def _insert_cost(conn, cost: float, model: str = "deepseek-v4-pro", created_at: str | None = None):
    created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO messages (conversation_id, role, content, model, tokens_in, tokens_out, cost, created_at)
           VALUES (1, 'assistant', 'x', ?, 100, 50, ?, ?)""",
        (model, cost, created_at),
    )


def test_cost_summary_aggregates(tmp_db):
    from database import get_cost_summary, get_db

    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        _insert_cost(conn, 0.5)
        _insert_cost(conn, 0.25, model="deepseek-v4-flash")

    s = get_cost_summary()
    assert s["today"]["msg_count"] == 2
    assert s["today"]["cost"] == pytest.approx(0.75)
    assert s["month"]["cost"] == pytest.approx(0.75)
    models = {m["model"]: m["cost"] for m in s["by_model_month"]}
    assert models["deepseek-v4-pro"] == pytest.approx(0.5)
    assert s["budget_monthly"] == config.LLM_BUDGET_MONTHLY


def test_budget_alert_thresholds_and_dedupe(tmp_db, monkeypatch):
    from database import get_db, get_unread_notifications
    from scripts.db_maintenance import check_llm_budget

    monkeypatch.setattr("config.LLM_BUDGET_MONTHLY", 10.0)
    monkeypatch.setattr("config.LLM_BUDGET_ALERT_PCT", 80)
    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        _insert_cost(conn, 8.5)   # 85 % du budget

    first = check_llm_budget()
    assert first is not None and first["threshold"] == 80
    # rejouer le job ne recrée pas d'alerte pour le même seuil/mois
    assert check_llm_budget() is None
    titles = [n["title"] for n in get_unread_notifications(20)]
    assert any(t.startswith("Budget LLM 80%") for t in titles)

    # dépassement → nouveau seuil 100 %, priorité high
    with get_db() as conn:
        _insert_cost(conn, 2.0)   # total 10.5 = 105 %
    second = check_llm_budget()
    assert second is not None and second["threshold"] == 100
    assert check_llm_budget() is None


def test_budget_disabled_when_zero(tmp_db, monkeypatch):
    from scripts.db_maintenance import check_llm_budget

    monkeypatch.setattr("config.LLM_BUDGET_MONTHLY", 0.0)
    assert check_llm_budget() is None


# ── Heures calmes ────────────────────────────────────────────

def _at(h: int, m: int) -> datetime:
    return datetime(2026, 7, 9, h, m)


def test_quiet_hours_overnight_range(monkeypatch):
    monkeypatch.setattr("config.QUIET_HOURS_START", "23:30")
    monkeypatch.setattr("config.QUIET_HOURS_END", "07:00")
    assert config.is_quiet_hours(_at(23, 45)) is True
    assert config.is_quiet_hours(_at(3, 0)) is True
    assert config.is_quiet_hours(_at(6, 59)) is True
    assert config.is_quiet_hours(_at(7, 0)) is False
    assert config.is_quiet_hours(_at(12, 0)) is False
    assert config.is_quiet_hours(_at(23, 29)) is False


def test_quiet_hours_daytime_range(monkeypatch):
    monkeypatch.setattr("config.QUIET_HOURS_START", "13:00")
    monkeypatch.setattr("config.QUIET_HOURS_END", "14:00")
    assert config.is_quiet_hours(_at(13, 30)) is True
    assert config.is_quiet_hours(_at(14, 0)) is False


def test_quiet_hours_disabled_or_invalid(monkeypatch):
    monkeypatch.setattr("config.QUIET_HOURS_START", "")
    monkeypatch.setattr("config.QUIET_HOURS_END", "")
    assert config.is_quiet_hours(_at(3, 0)) is False
    monkeypatch.setattr("config.QUIET_HOURS_START", "n'importe quoi")
    monkeypatch.setattr("config.QUIET_HOURS_END", "07:00")
    assert config.is_quiet_hours(_at(3, 0)) is False
    # bornes identiques = désactivé
    monkeypatch.setattr("config.QUIET_HOURS_START", "08:00")
    monkeypatch.setattr("config.QUIET_HOURS_END", "08:00")
    assert config.is_quiet_hours(_at(8, 0)) is False
