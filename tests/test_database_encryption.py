"""Migration SQLCipher, retour arrière et runtime fail-closed."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def encryption_passphrase(monkeypatch: pytest.MonkeyPatch) -> str:
    value = "test-sqlcipher-passphrase-32-characters"
    monkeypatch.setattr("config.DATABASE_ENCRYPTION_PASSPHRASE", value)
    return value


def _seed_plaintext(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA user_version = 17;
            CREATE TABLE secrets (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE VIRTUAL TABLE secrets_fts USING fts5(value);
            INSERT INTO secrets (value) VALUES ('donnée très privée');
            INSERT INTO secrets_fts (value) VALUES ('donnée très privée');
            """
        )


def test_sqlcipher_round_trip_preserves_schema_fts_and_rollback(
    tmp_path: Path,
    encryption_passphrase: str,
) -> None:
    from database.encryption import (
        connect_encrypted_database,
        database_encryption_status,
        disable_database_encryption,
        enable_database_encryption,
        export_plaintext_snapshot,
        replace_database_from_plaintext,
    )

    db_path = tmp_path / "jarvis.db"
    _seed_plaintext(db_path)

    enabled = enable_database_encryption(db_path, "default")
    assert enabled["changed"] is True
    assert database_encryption_status(db_path) == "encrypted"
    rollback = Path(enabled["rollback"])
    assert rollback.read_bytes().startswith(b"SQLite format 3\x00")

    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(db_path).execute("SELECT * FROM secrets").fetchall()

    encrypted = connect_encrypted_database(db_path, profile_id="default")
    try:
        assert encrypted.execute("PRAGMA user_version").fetchone()[0] == 17
        assert encrypted.execute("SELECT value FROM secrets").fetchone()[0] == "donnée très privée"
        assert encrypted.execute(
            "SELECT value FROM secrets_fts WHERE secrets_fts MATCH 'privée'"
        ).fetchone()[0] == "donnée très privée"
    finally:
        encrypted.close()

    snapshot = tmp_path / "backup.db"
    export_plaintext_snapshot(db_path, snapshot, "default")
    assert snapshot.read_bytes().startswith(b"SQLite format 3\x00")
    with sqlite3.connect(snapshot) as plain_snapshot:
        assert plain_snapshot.execute("SELECT value FROM secrets").fetchone()[0] == "donnée très privée"

    encrypted = connect_encrypted_database(db_path, profile_id="default")
    try:
        encrypted.execute("UPDATE secrets SET value = 'modifiée'")
        encrypted.commit()
    finally:
        encrypted.close()
    replace_database_from_plaintext(snapshot, db_path, "default")
    restored = connect_encrypted_database(db_path, profile_id="default")
    try:
        assert restored.execute("SELECT value FROM secrets").fetchone()[0] == "donnée très privée"
    finally:
        restored.close()

    disabled = disable_database_encryption(db_path, "default")
    assert disabled["changed"] is True
    assert database_encryption_status(db_path) == "plaintext"
    assert not Path(disabled["rollback"]).read_bytes().startswith(b"SQLite format 3\x00")
    with sqlite3.connect(db_path) as plain:
        assert plain.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert plain.execute("PRAGMA user_version").fetchone()[0] == 17
        assert plain.execute("SELECT value FROM secrets").fetchone()[0] == "donnée très privée"


def test_runtime_driver_creates_encrypted_database_and_rejects_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encryption_passphrase: str,
) -> None:
    import config
    from database import dbapi
    from database.encryption import DatabaseEncryptionError, database_encryption_status

    encrypted_path = tmp_path / "fresh.db"
    monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", True)
    importlib.reload(dbapi)
    try:
        with dbapi.connect(str(encrypted_path), profile_id="default") as conn:
            conn.execute("CREATE TABLE proof (value TEXT)")
            conn.execute("INSERT INTO proof VALUES ('secret')")
        assert database_encryption_status(encrypted_path) == "encrypted"

        plaintext_path = tmp_path / "plain.db"
        _seed_plaintext(plaintext_path)
        with pytest.raises(DatabaseEncryptionError, match="migrez-la"):
            dbapi.connect(str(plaintext_path), profile_id="default")
    finally:
        monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", False)
        importlib.reload(dbapi)


def test_wrong_sqlcipher_key_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encryption_passphrase: str,
) -> None:
    import config
    from database.encryption import (
        DatabaseEncryptionError,
        connect_encrypted_database,
        enable_database_encryption,
    )

    db_path = tmp_path / "jarvis.db"
    _seed_plaintext(db_path)
    enable_database_encryption(db_path, "default")
    monkeypatch.setattr(config, "DATABASE_ENCRYPTION_PASSPHRASE", "wrong-passphrase-with-enough-length")

    with pytest.raises(DatabaseEncryptionError, match="incorrecte ou base corrompue"):
        connect_encrypted_database(db_path, profile_id="default")


def test_existing_encrypted_database_never_rotates_its_key_implicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import encryption

    db_path = tmp_path / "jarvis.db"
    db_path.write_bytes(b"SQLCipher payload")
    create_values: list[bool] = []

    def _load_key(_profile_id: str, *, create: bool = False) -> str:
        create_values.append(create)
        return "existing-key-longer-than-20-characters"

    monkeypatch.setattr(encryption, "load_database_key", _load_key)
    monkeypatch.setattr(encryption, "_verify_encrypted", lambda _path, _key: None)

    result = encryption.enable_database_encryption(db_path, "default")
    assert result == {"ok": True, "status": "encrypted", "changed": False}
    assert create_values == [False]


def test_enable_fails_closed_when_wal_cannot_be_checkpointed(
    tmp_path: Path,
    encryption_passphrase: str,
) -> None:
    from database.encryption import DatabaseEncryptionError, enable_database_encryption

    db_path = tmp_path / "jarvis.db"
    writer = sqlite3.connect(db_path)
    reader = sqlite3.connect(db_path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE secrets (value TEXT NOT NULL)")
        writer.execute("INSERT INTO secrets VALUES ('avant')")
        writer.commit()
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM secrets").fetchall()
        writer.execute("INSERT INTO secrets VALUES ('après')")
        writer.commit()

        with pytest.raises(DatabaseEncryptionError, match="Checkpoint WAL impossible"):
            enable_database_encryption(db_path, "default")
    finally:
        reader.close()
        writer.close()


def test_full_jarvis_schema_runs_on_sqlcipher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encryption_passphrase: str,
) -> None:
    import config
    import database
    from database import dbapi
    from database.encryption import disable_database_encryption, enable_database_encryption

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    with sqlite3.connect(db_path) as conn:
        plaintext_tables = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]

    enable_database_encryption(db_path, "default")
    monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", True)
    importlib.reload(dbapi)
    try:
        database.init_db()
        task_id = database.create_task("preuve SQLCipher", priority=2)
        assert any(task["id"] == task_id for task in database.get_tasks())
        with database.get_connection() as conn:
            encrypted_tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
            options = {row[0] for row in conn.execute("PRAGMA compile_options")}
        assert encrypted_tables == plaintext_tables
        assert "ENABLE_FTS5" in options
        disable_database_encryption(db_path, "default")
    finally:
        monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", False)
        importlib.reload(dbapi)


def test_new_user_profile_gets_an_isolated_encrypted_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encryption_passphrase: str,
) -> None:
    import config
    import database
    from database import dbapi
    from database.encryption import database_encryption_status, enable_database_encryption

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    enable_database_encryption(db_path, "default")
    monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", True)
    importlib.reload(dbapi)
    try:
        alice = database.create_user_profile("Alice")
        alice_db = database.profile_database_path(alice["id"])
        assert database_encryption_status(alice_db) == "encrypted"

        with database.use_profile(alice["id"]):
            task_id = database.create_task("Secret Alice")
            assert [task["id"] for task in database.get_tasks()] == [task_id]
        with database.use_profile("default"):
            assert database.get_tasks() == []
    finally:
        monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", False)
        importlib.reload(dbapi)


def test_encrypted_backup_restores_into_sqlcipher_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encryption_passphrase: str,
) -> None:
    import config
    import database
    from database import dbapi
    from database.encryption import database_encryption_status, enable_database_encryption
    from scripts.db_maintenance import restore_backup, run_backup

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_PASSPHRASE", "backup-passphrase-32-characters-long")
    database.init_db()
    enable_database_encryption(db_path, "default")
    monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", True)
    importlib.reload(dbapi)
    try:
        kept_id = database.create_task("conservée", priority=1)
        backup = run_backup()
        assert backup["ok"] is True
        assert backup["encrypted"] is True

        removed_id = database.create_task("après sauvegarde", priority=1)
        restored = restore_backup(Path(backup["path"]).name)
        assert restored["ok"] is True
        task_ids = {task["id"] for task in database.get_tasks()}
        assert kept_id in task_ids
        assert removed_id not in task_ids
        assert database_encryption_status(db_path) == "encrypted"
    finally:
        monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", False)
        importlib.reload(dbapi)


def test_readonly_connections_cannot_create_or_mutate_databases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encryption_passphrase: str,
) -> None:
    import config
    from database import dbapi
    from database.encryption import enable_database_encryption

    missing = tmp_path / "missing.db"
    with pytest.raises(dbapi.OperationalError):
        dbapi.connect_readonly(missing)
    assert not missing.exists()

    db_path = tmp_path / "jarvis.db"
    _seed_plaintext(db_path)
    enable_database_encryption(db_path, "default")
    monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", True)
    importlib.reload(dbapi)
    try:
        with dbapi.connect_readonly(db_path) as conn:
            assert conn.execute("SELECT value FROM secrets").fetchone()[0]
            with pytest.raises(dbapi.OperationalError):
                conn.execute("INSERT INTO secrets (value) VALUES ('interdit')")
    finally:
        monkeypatch.setattr(config, "DATABASE_ENCRYPTION_ENABLED", False)
        importlib.reload(dbapi)


def test_short_explicit_passphrase_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from database.encryption import DatabaseEncryptionError, load_database_key

    monkeypatch.setattr("config.DATABASE_ENCRYPTION_PASSPHRASE", "trop-court")
    with pytest.raises(DatabaseEncryptionError, match="20 caractères"):
        load_database_key("default")


def test_keychain_secret_is_sent_on_stdin_not_in_process_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import encryption

    captured: dict = {}

    def _run(args: list[str], **kwargs):
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        return encryption.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(encryption.subprocess, "run", _run)
    secret = "secret-that-must-never-appear-in-argv"
    encryption._keychain_command(
        "add-generic-password",
        "-a",
        "default",
        "-w",
        secret_input=f"{secret}\n",
    )

    assert secret not in captured["args"]
    assert captured["input"] == f"{secret}\n"
