"""Tests des migrations SQLite versionnées (backup préalable, intégrité, ordre)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.BACKUP_DIR", str(tmp_path / "backups"))

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    monkeypatch.setattr("config.DB_MIGRATIONS_DIR", str(migrations_dir))

    from database import init_db

    init_db()
    return migrations_dir


def _write(migrations_dir: Path, name: str, sql: str) -> Path:
    p = migrations_dir / name
    p.write_text(sql, encoding="utf-8")
    return p


def test_no_pending_migrations_is_noop(tmp_env):
    from scripts.db_migrations import apply_pending_migrations

    report = apply_pending_migrations()
    assert report == {"ok": True, "applied": [], "backup": None, "error": None}


def test_applies_in_order_with_backup(tmp_env):
    from database import get_db
    from scripts.db_migrations import apply_pending_migrations, migration_status

    _write(tmp_env, "0002_second.sql", "INSERT INTO my_migration_table (key, value) VALUES ('b', '2');")
    _write(tmp_env, "0001_first.sql",
          "CREATE TABLE my_migration_table (key TEXT PRIMARY KEY, value TEXT);"
          "INSERT INTO my_migration_table (key, value) VALUES ('a', '1');")

    status_before = migration_status()
    assert status_before["pending"] == ["0001_first.sql", "0002_second.sql"]

    report = apply_pending_migrations()
    assert report["ok"] is True
    assert report["applied"] == ["0001_first.sql", "0002_second.sql"]
    assert report["backup"]["ok"] is True
    assert Path(report["backup"]["path"]).exists()

    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM my_migration_table ORDER BY key").fetchall()
    assert [dict(r) for r in rows] == [{"key": "a", "value": "1"}, {"key": "b", "value": "2"}]

    # rejouer : rien à appliquer, pas de nouvelle sauvegarde
    report2 = apply_pending_migrations()
    assert report2 == {"ok": True, "applied": [], "backup": None, "error": None}
    assert migration_status()["pending"] == []


def test_stops_at_first_failure(tmp_env):
    from database import get_db
    from scripts.db_migrations import apply_pending_migrations

    _write(tmp_env, "0001_ok.sql",
          "CREATE TABLE t (id INTEGER PRIMARY KEY);"
          "INSERT INTO t (id) VALUES (1);")
    _write(tmp_env, "0002_bad.sql", "INSERT INTO table_qui_nexiste_pas (x) VALUES (1);")
    _write(tmp_env, "0003_never_reached.sql", "CREATE TABLE never_reached (id INTEGER);")

    report = apply_pending_migrations()
    assert report["ok"] is False
    assert report["applied"] == ["0001_ok.sql"]
    assert "0002_bad.sql" in report["error"]

    with get_db() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "t" in tables
    assert "never_reached" not in tables

    # 0001 ne sera pas rejouée ; 0002/0003 restent en attente pour la prochaine tentative
    from scripts.db_migrations import migration_status
    assert migration_status()["pending"] == ["0002_bad.sql", "0003_never_reached.sql"]


def test_integrity_error_on_tampered_migration(tmp_env):
    from scripts.db_migrations import MigrationIntegrityError, apply_pending_migrations, pending_migrations

    path = _write(tmp_env, "0001_a.sql", "CREATE TABLE t (id INTEGER);")
    report = apply_pending_migrations()
    assert report["ok"] is True

    path.write_text("CREATE TABLE t (id INTEGER); DROP TABLE t;", encoding="utf-8")
    with pytest.raises(MigrationIntegrityError):
        pending_migrations()

    # apply_pending_migrations capture l'erreur au lieu de laisser lever
    report2 = apply_pending_migrations()
    assert report2["ok"] is False
    assert "0001_a.sql" in report2["error"]


def test_backup_failure_blocks_migrations(tmp_env, monkeypatch):
    from scripts.db_migrations import apply_pending_migrations

    _write(tmp_env, "0001_a.sql", "CREATE TABLE t (id INTEGER);")
    monkeypatch.setattr(
        "scripts.db_maintenance.run_backup",
        lambda: {"ok": False, "error": "disque plein"},
    )
    report = apply_pending_migrations()
    assert report["ok"] is False
    assert "disque plein" in report["error"]

    # rien n'a été appliqué
    from scripts.db_migrations import migration_status
    assert migration_status()["pending"] == ["0001_a.sql"]


def test_startup_hook_never_raises_on_failure(tmp_env, monkeypatch, caplog):
    from scripts.db_migrations import run_startup_migrations

    _write(tmp_env, "0001_bad.sql", "INSERT INTO nope (x) VALUES (1);")
    run_startup_migrations()  # ne doit jamais lever


def test_startup_hook_respects_auto_apply_flag(tmp_env, monkeypatch):
    from scripts.db_migrations import migration_status, run_startup_migrations

    _write(tmp_env, "0001_a.sql", "CREATE TABLE t (id INTEGER);")
    monkeypatch.setattr("config.DB_MIGRATIONS_AUTO_APPLY", False)
    run_startup_migrations()
    assert migration_status()["pending"] == ["0001_a.sql"]  # rien appliqué
