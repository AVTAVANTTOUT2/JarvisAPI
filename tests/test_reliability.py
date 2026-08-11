"""Tests fiabilité — sauvegardes SQLite, rétention, budget LLM, heures calmes."""

from __future__ import annotations

import sqlite3
import stat
import sys
from datetime import datetime, timezone
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
    monkeypatch.setattr(
        "config.BACKUP_ENCRYPTION_KEY_FILE",
        str(tmp_path / ".backup_encryption.key"),
    )
    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr("config.BACKUP_ENCRYPTION_PASSPHRASE", "")
    from database import init_db

    init_db()
    return db_path


# ── Sauvegardes ──────────────────────────────────────────────

def test_backup_creates_valid_snapshot(tmp_db, tmp_path):
    from scripts.db_maintenance import (
        _decrypt_backup_bytes,
        _validated_restore_source,
        list_backups,
        run_backup,
    )

    report = run_backup()
    assert report["ok"] is True
    assert report["encrypted"] is True
    dest = Path(report["path"])
    assert dest.exists() and report["size_bytes"] > 0

    # Le snapshot déchiffré est une base SQLite intègre contenant les tables JARVIS.
    source = _validated_restore_source(
        _decrypt_backup_bytes(dest),
        Path(config.BACKUP_DIR),
    )
    try:
        conn = sqlite3.connect(source)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        conn.close()
    finally:
        source.unlink(missing_ok=True)
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


def test_backup_listing_rotation_and_restore_are_profile_isolated(
    tmp_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from database import use_profile
    from scripts.db_maintenance import _rotate_backups, list_backups, restore_backup

    backup_dir = Path(config.BACKUP_DIR)
    backup_dir.mkdir(parents=True)
    default_old = backup_dir / "jarvis-20260811-010101.db.enc"
    default_new = backup_dir / "jarvis-20260811-020202.db.enc"
    alice = backup_dir / "jarvis-alice-20260811-030303.db.enc"
    for index, path in enumerate((default_old, default_new, alice), start=1):
        path.write_bytes(b"private backup")
        os.utime(path, (index, index))

    assert {item["name"] for item in list_backups()} == {
        default_old.name,
        default_new.name,
    }
    assert _rotate_backups(backup_dir, keep=1) == [default_old.name]
    assert alice.exists()

    with use_profile("alice"):
        assert [item["name"] for item in list_backups()] == [alice.name]
        assert restore_backup(default_new.name) == {
            "ok": False,
            "error": "Sauvegarde introuvable",
        }


def test_backup_missing_db_fails_cleanly(tmp_db, monkeypatch, tmp_path):
    from scripts.db_maintenance import run_backup

    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "absente.db"))
    report = run_backup()
    assert report["ok"] is False and "introuvable" in report["error"]


# ── Chiffrement + restauration des sauvegardes ───────────────

def test_backup_encrypted_by_default_with_private_generated_key(tmp_db):
    from scripts.db_maintenance import run_backup

    assert config.BACKUP_ENCRYPTION_ENABLED is True
    report = run_backup()
    assert report["ok"] is True
    assert report["encrypted"] is True
    dest = Path(report["path"])
    assert dest.suffix == ".enc"
    # Le fichier chiffré n'est pas une base SQLite lisible en clair
    assert not dest.read_bytes().startswith(b"SQLite format 3")
    key_path = Path(config.BACKUP_ENCRYPTION_KEY_FILE)
    assert key_path.is_file()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700


def test_backup_fails_closed_when_encryption_key_is_unavailable(
    tmp_db,
    monkeypatch,
):
    import scripts.db_maintenance as maintenance

    monkeypatch.setattr(
        maintenance,
        "_backup_secret_candidates",
        lambda **_kwargs: [],
    )
    report = maintenance.run_backup()
    assert report["ok"] is False
    assert "Chiffrement" in report["error"]
    assert list(Path(config.BACKUP_DIR).glob("jarvis-*.db*")) == []


def test_restore_roundtrip_encrypted_backup(tmp_db):
    from database import create_task, get_db
    from scripts.db_maintenance import restore_backup, run_backup

    create_task("Tâche avant sauvegarde")
    report = run_backup()
    backup_name = Path(report["path"]).name

    # Modifie la base courante après la sauvegarde
    create_task("Tâche après sauvegarde (doit disparaître après restore)")

    result = restore_backup(backup_name)
    assert result["ok"] is True
    assert result["safety_backup"]

    with get_db() as conn:
        titles = {r["title"] for r in conn.execute("SELECT title FROM tasks")}
    assert titles == {"Tâche avant sauvegarde"}


def test_restore_plain_unencrypted_backup(tmp_db, monkeypatch):
    from database import create_task, get_db
    from scripts.db_maintenance import restore_backup, run_backup

    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", False)
    create_task("Avant")
    report = run_backup()
    backup_name = Path(report["path"]).name
    create_task("Après")

    result = restore_backup(backup_name)
    assert result["ok"] is True
    with get_db() as conn:
        titles = {r["title"] for r in conn.execute("SELECT title FROM tasks")}
    assert titles == {"Avant"}


def test_restore_wrong_passphrase_fails_cleanly(tmp_db, monkeypatch):
    from scripts.db_maintenance import restore_backup, run_backup

    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr("config.BACKUP_ENCRYPTION_PASSPHRASE", "correct-passphrase")
    report = run_backup()
    backup_name = Path(report["path"]).name

    monkeypatch.setattr("config.BACKUP_ENCRYPTION_PASSPHRASE", "wrong-passphrase")
    result = restore_backup(backup_name)
    assert result["ok"] is False
    assert "Déchiffrement" in result["error"]


def test_restore_rejects_corrupt_sqlite_before_safety_snapshot(tmp_db, monkeypatch):
    from core.file_security import write_private_bytes
    from scripts.db_maintenance import restore_backup

    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", False)
    corrupt = Path(config.BACKUP_DIR) / "jarvis-corrupt.db"
    write_private_bytes(corrupt, b"not-a-sqlite-database", exclusive=True)

    result = restore_backup(corrupt.name)

    assert result["ok"] is False
    assert result["error"] == "Sauvegarde SQLite invalide"
    assert list(Path(config.BACKUP_DIR).glob("jarvis-*.db*")) == [corrupt]


def test_restore_legacy_encrypted_backup(tmp_db, monkeypatch):
    from cryptography.fernet import Fernet

    from database import create_task, get_db
    from scripts.db_maintenance import (
        _derive_legacy_fernet_key,
        restore_backup,
        run_backup,
    )
    from core.file_security import write_private_bytes

    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(
        "config.BACKUP_ENCRYPTION_PASSPHRASE",
        "legacy-passphrase",
    )
    create_task("Avant legacy")
    plain = run_backup()
    plain_path = Path(plain["path"])
    legacy_path = plain_path.with_suffix(".db.enc")
    token = Fernet(_derive_legacy_fernet_key("legacy-passphrase")).encrypt(
        plain_path.read_bytes()
    )
    write_private_bytes(legacy_path, token, exclusive=True)
    plain_path.unlink()
    create_task("Après legacy")

    result = restore_backup(legacy_path.name)

    assert result["ok"] is True
    with get_db() as conn:
        titles = {row["title"] for row in conn.execute("SELECT title FROM tasks")}
    assert titles == {"Avant legacy"}


def test_restore_rejects_path_traversal(tmp_db):
    from scripts.db_maintenance import restore_backup

    result = restore_backup("../../etc/passwd")
    assert result["ok"] is False
    assert "introuvable" in result["error"]


def test_restore_rejects_unknown_backup(tmp_db):
    from scripts.db_maintenance import restore_backup

    result = restore_backup("jarvis-does-not-exist.db")
    assert result["ok"] is False


def test_list_backups_reports_encrypted_flag(tmp_db, monkeypatch):
    from scripts.db_maintenance import list_backups, run_backup

    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr("config.BACKUP_ENCRYPTION_PASSPHRASE", "secret")
    run_backup()

    backups = list_backups()
    assert backups[0]["encrypted"] is True


def test_database_and_sidecars_are_private(tmp_db):
    from database import get_db

    with get_db() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('mode', 'private')")

    assert stat.S_IMODE(tmp_db.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{tmp_db}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


def test_existing_backup_and_key_permissions_are_hardened(tmp_db):
    from scripts.db_maintenance import harden_backup_permissions

    backup_dir = Path(config.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "jarvis-legacy.db.enc"
    backup.write_bytes(b"legacy")
    key = Path(config.BACKUP_ENCRYPTION_KEY_FILE)
    key.write_text("secret", encoding="utf-8")
    backup_dir.chmod(0o755)
    backup.chmod(0o644)
    key.chmod(0o644)

    harden_backup_permissions()

    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


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


def test_maintenance_purges_scheduler_job_runs_on_same_connection(tmp_db, monkeypatch):
    """La purge des runs scheduler doit rester dans la connexion ouverte.

    Un appel imbriqué à ``get_db()`` / ``purge_scheduler_runs`` pendant
    ``run_maintenance`` deadlock SQLite (même thread, connexion déjà tenue).
    """
    from database import get_db
    from scripts.db_maintenance import run_maintenance

    monkeypatch.setattr("config.RETENTION_SCHEDULER_RUNS_DAYS", 7)
    nested_calls: list[int] = []

    def _must_not_nest(days: int) -> int:
        nested_calls.append(days)
        raise AssertionError(
            "purge_scheduler_runs ne doit plus être appelé depuis run_maintenance"
        )

    monkeypatch.setattr(
        "database.scheduler_runs.purge_scheduler_runs",
        _must_not_nest,
    )

    with get_db() as conn:
        conn.execute(
            """INSERT INTO scheduler_job_runs
               (job_id, trigger, status, started_at)
               VALUES ('morning_briefing', 'cron', 'ok',
                       datetime('now', '-30 days'))"""
        )
        conn.execute(
            """INSERT INTO scheduler_job_runs
               (job_id, trigger, status, started_at)
               VALUES ('morning_briefing', 'cron', 'ok',
                       datetime('now', '-1 days'))"""
        )

    report = run_maintenance()
    assert nested_calls == []
    assert report["purged"]["scheduler_job_runs"] == 1

    with get_db() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM scheduler_job_runs"
        ).fetchone()[0]
    assert remaining == 1


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
    created_at = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
