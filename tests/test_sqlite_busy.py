"""Tests de concurrence et de configuration du busy_timeout SQLite (ADR-004).

Vérifie que toutes les connexions applicatives à jarvis.db reçoivent le
PRAGMA busy_timeout configuré, que la valeur est validée, et que deux
écritures concurrentes se résolvent sans SQLITE_BUSY grâce au timeout.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import config
import database
from database.core import _validated_busy_timeout_ms, _DEFAULT_BUSY_TIMEOUT_MS


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirige database.DB_PATH vers un fichier temporaire et initialise le schéma."""
    db_file = tmp_path / "test_busy.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()
    return db_file


# ──────────────────────────────────────────────────────────
# 1. PRAGMA busy_timeout est présent et configuré
# ──────────────────────────────────────────────────────────


def test_pragma_busy_timeout_present(tmp_db):
    """La connexion a un busy_timeout non nul."""
    conn = database.get_connection()
    try:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert val > 0


def test_pragma_busy_timeout_default_value(tmp_db):
    """Avec la config par défaut, busy_timeout vaut 5000 ms."""
    conn = database.get_connection()
    try:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert val == 5000


# ──────────────────────────────────────────────────────────
# 2. La valeur config est respectée
# ──────────────────────────────────────────────────────────


def test_custom_busy_timeout_value(tmp_db, monkeypatch):
    """Une valeur custom dans config est répercutée dans le PRAGMA."""
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", 8000)
    conn = database.get_connection()
    try:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert val == 8000


# ──────────────────────────────────────────────────────────
# 3. Validation des valeurs invalides
# ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_value", [0, -1, -5000])
def test_invalid_timeout_falls_back_to_default(tmp_db, monkeypatch, bad_value):
    """Une valeur <= 0 provoque un fallback au défaut (5000 ms)."""
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", bad_value)
    conn = database.get_connection()
    try:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert val == _DEFAULT_BUSY_TIMEOUT_MS


def test_validated_busy_timeout_ms_returns_config_when_valid(monkeypatch):
    """_validated_busy_timeout_ms retourne la valeur config si valide."""
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", 3000)
    assert _validated_busy_timeout_ms() == 3000


def test_validated_busy_timeout_ms_returns_default_on_zero(monkeypatch):
    """_validated_busy_timeout_ms retourne le défaut pour 0."""
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", 0)
    assert _validated_busy_timeout_ms() == _DEFAULT_BUSY_TIMEOUT_MS


# ──────────────────────────────────────────────────────────
# 4. Pas de régression WAL et foreign_keys
# ──────────────────────────────────────────────────────────


def test_wal_mode_active(tmp_db):
    """Le mode WAL est actif sur chaque connexion."""
    conn = database.get_connection()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_foreign_keys_enabled(tmp_db):
    """Les foreign keys sont activées sur chaque connexion."""
    conn = database.get_connection()
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert fk == 1


# ──────────────────────────────────────────────────────────
# 5. Concurrence : écriture qui attend puis réussit
# ──────────────────────────────────────────────────────────


def test_concurrent_write_waits_then_succeeds(tmp_db):
    """Deux écritures concurrentes : la seconde attend le busy_timeout
    et réussit une fois la première commitée, au lieu de lever SQLITE_BUSY."""

    db_str = str(tmp_db)
    conn1 = sqlite3.connect(db_str)
    conn1.execute("PRAGMA busy_timeout = 5000")
    conn1.execute("PRAGMA journal_mode=WAL")
    conn1.execute(
        "CREATE TABLE IF NOT EXISTS busy_test (id INTEGER PRIMARY KEY, val TEXT)"
    )
    conn1.commit()

    lock_acquired = threading.Event()
    conn1.execute("BEGIN IMMEDIATE")
    conn1.execute("INSERT INTO busy_test (val) VALUES ('writer1')")
    lock_acquired.set()

    errors: list[Exception] = []
    success = threading.Event()

    def writer2():
        lock_acquired.wait()
        c2 = sqlite3.connect(db_str)
        c2.execute("PRAGMA busy_timeout = 5000")
        c2.execute("PRAGMA journal_mode=WAL")
        try:
            c2.execute("BEGIN IMMEDIATE")
            c2.execute("INSERT INTO busy_test (val) VALUES ('writer2')")
            c2.commit()
            success.set()
        except Exception as exc:
            errors.append(exc)
        finally:
            c2.close()

    t = threading.Thread(target=writer2)
    t.start()

    time.sleep(0.15)
    conn1.commit()
    conn1.close()

    t.join(timeout=6)

    assert not errors, f"Writer 2 a échoué : {errors}"
    assert success.is_set(), "Writer 2 n'a pas terminé"


def test_concurrent_write_without_timeout_raises(tmp_db):
    """Sans busy_timeout, une écriture concurrente lève sqlite3.OperationalError."""

    db_str = str(tmp_db)
    conn1 = sqlite3.connect(db_str)
    conn1.execute("PRAGMA journal_mode=WAL")
    conn1.execute(
        "CREATE TABLE IF NOT EXISTS busy_test2 (id INTEGER PRIMARY KEY, val TEXT)"
    )
    conn1.commit()

    lock_acquired = threading.Event()
    conn1.execute("BEGIN IMMEDIATE")
    conn1.execute("INSERT INTO busy_test2 (val) VALUES ('holder')")
    lock_acquired.set()

    errors: list[Exception] = []

    def writer2():
        lock_acquired.wait()
        c2 = sqlite3.connect(db_str)
        c2.execute("PRAGMA busy_timeout = 0")
        c2.execute("PRAGMA journal_mode=WAL")
        try:
            c2.execute("BEGIN IMMEDIATE")
            errors.append(None)
        except sqlite3.OperationalError as exc:
            errors.append(exc)
        finally:
            c2.close()

    t = threading.Thread(target=writer2)
    t.start()
    t.join(timeout=6)

    conn1.rollback()
    conn1.close()

    assert len(errors) == 1
    assert isinstance(errors[0], sqlite3.OperationalError)
    assert "locked" in str(errors[0])


# ──────────────────────────────────────────────────────────
# 6. get_db context manager fonctionne avec busy_timeout
# ──────────────────────────────────────────────────────────


def test_get_db_context_manager_has_busy_timeout(tmp_db):
    """Le context manager get_db() fournit une connexion avec busy_timeout."""
    from database.core import get_db

    with get_db() as conn:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert val == 5000
