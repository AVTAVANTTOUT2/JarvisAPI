"""Chiffrement SQLCipher, gestion de clé et migrations atomiques SQLite."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sqlite3 as plaintext_sqlite
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from core.file_security import ensure_private_directory, ensure_private_file

_SQLITE_HEADER = b"SQLite format 3\x00"
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MIN_PASSPHRASE_LENGTH = 20
_SQLCIPHER_DRIVER: Any | None = None


class DatabaseEncryptionError(RuntimeError):
    """Erreur fail-closed de clé, de pilote ou de migration SQLCipher."""


def _profile_id(value: str) -> str:
    candidate = str(value).strip().casefold()
    if not _PROFILE_ID_RE.fullmatch(candidate):
        raise DatabaseEncryptionError("Identifiant de profil invalide")
    return candidate


def sqlcipher_driver() -> Any:
    """Charge le pilote natif uniquement lorsque le chiffrement est utilisé."""
    global _SQLCIPHER_DRIVER
    if _SQLCIPHER_DRIVER is not None:
        return _SQLCIPHER_DRIVER
    try:
        from sqlcipher3 import dbapi2 as driver
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        raise DatabaseEncryptionError(
            "SQLCipher indisponible : installez sqlcipher3==0.6.2"
        ) from exc
    probe = driver.connect(":memory:")
    try:
        options = {row[0] for row in probe.execute("PRAGMA compile_options")}
        cipher_version = probe.execute("PRAGMA cipher_version").fetchone()
    finally:
        probe.close()
    if "ENABLE_FTS5" not in options or not cipher_version or not cipher_version[0]:
        raise DatabaseEncryptionError(
            "Le pilote SQLCipher doit inclure FTS5 et une version cipher valide"
        )
    _SQLCIPHER_DRIVER = driver
    return driver


def _open_sqlcipher(path: str | Path, key: str, **kwargs: Any) -> Any:
    """Ouvre SQLCipher puis applique la clé comme toute première instruction."""
    driver = sqlcipher_driver()
    conn = driver.connect(str(path), **kwargs)
    escaped_key = key.replace("'", "''")
    conn.execute(f"PRAGMA key = '{escaped_key}'")
    return conn


def database_encryption_status(path: str | Path) -> str:
    """Retourne missing, plaintext ou encrypted sans tenter de déchiffrer."""
    db_path = Path(path)
    if not db_path.exists():
        return "missing"
    with db_path.open("rb") as handle:
        header = handle.read(len(_SQLITE_HEADER))
    return "plaintext" if header == _SQLITE_HEADER else "encrypted"


def _configured_passphrase() -> str | None:
    value = str(getattr(config, "DATABASE_ENCRYPTION_PASSPHRASE", "") or "")
    if not value:
        return None
    if len(value) < _MIN_PASSPHRASE_LENGTH:
        raise DatabaseEncryptionError(
            f"DATABASE_ENCRYPTION_PASSPHRASE doit contenir au moins "
            f"{_MIN_PASSPHRASE_LENGTH} caractères"
        )
    return value


def _keychain_command(
    *args: str,
    secret_input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/security", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        input=secret_input,
    )


def load_database_key(profile_id: str, *, create: bool = False) -> str:
    """Obtient la clé d'un profil sans jamais la journaliser.

    Une passphrase explicite sert aux environnements non-macOS/CI. En
    production macOS, la clé aléatoire vit dans le Trousseau de connexion.
    """
    configured = _configured_passphrase()
    if configured is not None:
        return configured

    selected = _profile_id(profile_id)
    if sys.platform != "darwin":
        raise DatabaseEncryptionError(
            "DATABASE_ENCRYPTION_PASSPHRASE est requise hors macOS"
        )
    service = str(config.DATABASE_ENCRYPTION_KEYCHAIN_SERVICE).strip()
    if not service:
        raise DatabaseEncryptionError("Service Trousseau SQLCipher vide")

    found = _keychain_command(
        "find-generic-password",
        "-a",
        selected,
        "-s",
        service,
        "-w",
    )
    if found.returncode == 0:
        value = found.stdout.strip()
        if len(value) < _MIN_PASSPHRASE_LENGTH:
            raise DatabaseEncryptionError("Clé SQLCipher du Trousseau invalide")
        return value
    if not create:
        raise DatabaseEncryptionError(
            f"Clé SQLCipher absente du Trousseau pour le profil {selected}"
        )

    value = secrets.token_urlsafe(48)
    stored = _keychain_command(
        "add-generic-password",
        "-U",
        "-a",
        selected,
        "-s",
        service,
        "-w",
        secret_input=f"{value}\n",
    )
    if stored.returncode != 0:
        raise DatabaseEncryptionError(
            f"Impossible d'enregistrer la clé SQLCipher du profil {selected}"
        )
    return value


def connect_encrypted_database(
    path: str | Path,
    *,
    profile_id: str,
    readonly: bool = False,
    **kwargs: Any,
) -> Any:
    """Ouvre une base SQLCipher et vérifie immédiatement la clé."""
    db_path = Path(path)
    status = database_encryption_status(db_path)
    if status == "plaintext":
        raise DatabaseEncryptionError(
            f"Base SQLite en clair : migrez-la avant activation ({db_path})"
        )
    if readonly and status == "missing":
        raise DatabaseEncryptionError(f"Base introuvable : {db_path}")
    key = load_database_key(profile_id, create=status == "missing")
    target: str | Path = db_path
    if readonly:
        target = f"{db_path.expanduser().resolve().as_uri()}?mode=ro"
        kwargs["uri"] = True
    conn = _open_sqlcipher(target, key, **kwargs)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception as exc:
        conn.close()
        raise DatabaseEncryptionError(
            f"Clé SQLCipher incorrecte ou base corrompue : {db_path}"
        ) from exc
    return conn


def _checkpoint_wal(conn: Any) -> None:
    result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if result and int(result[0]) != 0:
        raise DatabaseEncryptionError(
            "Checkpoint WAL impossible : arrêtez les processus qui utilisent la base"
        )


def _checkpoint_plaintext_database(path: Path) -> int:
    conn = plaintext_sqlite.connect(str(path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise DatabaseEncryptionError("La base SQLite source est corrompue")
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        _checkpoint_wal(conn)
        return user_version
    finally:
        conn.close()


def _remove_checkpointed_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def _migration_paths(path: Path, label: str) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    temporary = path.with_name(f".{path.name}.{label}.tmp")
    rollback = path.with_name(f"{path.name}.pre-{label}-{stamp}.bak")
    if temporary.exists():
        raise DatabaseEncryptionError(f"Migration temporaire déjà présente : {temporary}")
    suffix = 1
    while rollback.exists():
        rollback = path.with_name(f"{path.name}.pre-{label}-{stamp}-{suffix}.bak")
        suffix += 1
    return temporary, rollback


def _verify_encrypted(path: Path, key: str) -> None:
    conn = _open_sqlcipher(path, key)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise DatabaseEncryptionError("Contrôle d'intégrité SQLCipher en échec")
    finally:
        conn.close()


def export_plaintext_snapshot(
    source: str | Path,
    destination: str | Path,
    profile_id: str,
) -> None:
    """Exporte une image SQLite standard, même si la source est SQLCipher."""
    source_path = Path(source)
    destination_path = Path(destination)
    if destination_path.exists():
        raise DatabaseEncryptionError(f"Snapshot déjà présent : {destination_path}")
    status = database_encryption_status(source_path)
    if status == "missing":
        raise DatabaseEncryptionError(f"Base introuvable : {source_path}")
    if status == "plaintext":
        conn = plaintext_sqlite.connect(str(source_path))
        try:
            conn.execute("VACUUM INTO ?", (str(destination_path),))
        finally:
            conn.close()
    else:
        key = load_database_key(profile_id)
        conn = _open_sqlcipher(source_path, key)
        try:
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            conn.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(destination_path),))
            conn.execute("SELECT sqlcipher_export('plaintext')")
            conn.execute(f"PRAGMA plaintext.user_version = {user_version}")
            conn.execute("DETACH DATABASE plaintext")
        finally:
            conn.close()
    _checkpoint_plaintext_database(destination_path)
    ensure_private_file(destination_path)


def replace_database_from_plaintext(
    source: str | Path,
    destination: str | Path,
    profile_id: str,
) -> None:
    """Restaure une image plaintext vers le format actif du runtime."""
    source_path = Path(source)
    destination_path = Path(destination)
    destination_status = database_encryption_status(destination_path)
    if not config.DATABASE_ENCRYPTION_ENABLED and destination_status != "encrypted":
        with plaintext_sqlite.connect(str(source_path)) as source_conn:
            with plaintext_sqlite.connect(str(destination_path)) as destination_conn:
                source_conn.backup(destination_conn)
        ensure_private_file(destination_path)
        return

    selected = _profile_id(profile_id)
    key = load_database_key(selected, create=not destination_path.exists())
    user_version = _checkpoint_plaintext_database(source_path)
    temporary, _ = _migration_paths(destination_path, "restore")
    conn = None
    try:
        conn = _open_sqlcipher(temporary, key)
        conn.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(source_path),))
        conn.execute("SELECT sqlcipher_export('main', 'plaintext')")
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.execute("DETACH DATABASE plaintext")
        conn.close()
        conn = None
        _verify_encrypted(temporary, key)
        if destination_path.exists():
            current = _open_sqlcipher(destination_path, key)
            try:
                _checkpoint_wal(current)
            finally:
                current.close()
        _remove_checkpointed_sidecars(destination_path)
        os.replace(temporary, destination_path)
        ensure_private_file(destination_path)
    except Exception:
        if conn is not None:
            conn.close()
        temporary.unlink(missing_ok=True)
        raise


def enable_database_encryption(path: str | Path, profile_id: str) -> dict[str, Any]:
    """Convertit atomiquement une base plaintext vers SQLCipher."""
    db_path = Path(path)
    selected = _profile_id(profile_id)
    status = database_encryption_status(db_path)
    if status == "encrypted":
        key = load_database_key(selected)
        _verify_encrypted(db_path, key)
        return {"ok": True, "status": "encrypted", "changed": False}
    if status == "missing":
        raise DatabaseEncryptionError(f"Base introuvable : {db_path}")
    key = load_database_key(selected, create=True)

    ensure_private_directory(db_path.parent)
    user_version = _checkpoint_plaintext_database(db_path)
    temporary, rollback = _migration_paths(db_path, "sqlcipher")
    conn = None
    try:
        conn = _open_sqlcipher(temporary, key)
        conn.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(db_path),))
        conn.execute("SELECT sqlcipher_export('main', 'plaintext')")
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.execute("DETACH DATABASE plaintext")
        conn.close()
        conn = None
        ensure_private_file(temporary)
        _verify_encrypted(temporary, key)

        shutil.copy2(db_path, rollback)
        ensure_private_file(rollback)
        _remove_checkpointed_sidecars(db_path)
        os.replace(temporary, db_path)
        ensure_private_file(db_path)
    except Exception:
        if conn is not None:
            conn.close()
        temporary.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "status": "encrypted",
        "changed": True,
        "rollback": str(rollback),
    }


def disable_database_encryption(path: str | Path, profile_id: str) -> dict[str, Any]:
    """Exporte atomiquement SQLCipher vers SQLite standard, clé conservée."""
    db_path = Path(path)
    selected = _profile_id(profile_id)
    status = database_encryption_status(db_path)
    if status == "plaintext":
        return {"ok": True, "status": "plaintext", "changed": False}
    if status == "missing":
        raise DatabaseEncryptionError(f"Base introuvable : {db_path}")

    key = load_database_key(selected)
    temporary, rollback = _migration_paths(db_path, "plaintext")
    conn = _open_sqlcipher(db_path, key)
    try:
        _checkpoint_wal(conn)
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        conn.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(temporary),))
        conn.execute("SELECT sqlcipher_export('plaintext')")
        conn.execute(f"PRAGMA plaintext.user_version = {user_version}")
        conn.execute("DETACH DATABASE plaintext")
    finally:
        conn.close()

    try:
        _checkpoint_plaintext_database(temporary)
        ensure_private_file(temporary)
        shutil.copy2(db_path, rollback)
        ensure_private_file(rollback)
        _remove_checkpointed_sidecars(db_path)
        os.replace(temporary, db_path)
        ensure_private_file(db_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "status": "plaintext",
        "changed": True,
        "rollback": str(rollback),
    }
