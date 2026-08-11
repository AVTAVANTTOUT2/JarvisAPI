"""Pilote DB-API unique : sqlite3 standard ou SQLCipher fail-closed."""

from __future__ import annotations

import sqlite3 as _stdlib_sqlite
from pathlib import Path
from typing import Any

import config

if config.DATABASE_ENCRYPTION_ENABLED:
    from .encryption import sqlcipher_driver

    _driver = sqlcipher_driver()
else:
    _driver = _stdlib_sqlite

Error = _driver.Error
DatabaseError = _driver.DatabaseError
IntegrityError = _driver.IntegrityError
OperationalError = _driver.OperationalError
Connection = _driver.Connection
Row = _driver.Row
complete_statement = _driver.complete_statement
PARSE_DECLTYPES = _driver.PARSE_DECLTYPES
PARSE_COLNAMES = _driver.PARSE_COLNAMES


def connect(
    database: str,
    *args: Any,
    profile_id: str = "default",
    **kwargs: Any,
) -> Any:
    """Ouvre la base avec le pilote sélectionné au démarrage du processus."""
    if not config.DATABASE_ENCRYPTION_ENABLED:
        return _driver.connect(database, *args, **kwargs)
    from .encryption import connect_encrypted_database

    return connect_encrypted_database(
        database,
        profile_id=profile_id,
        *args,
        **kwargs,
    )


def connect_readonly(database: str | Any, *, profile_id: str = "default") -> Any:
    """Ouvre la base en mode fichier read-only, puis verrouille aussi les requêtes."""
    if config.DATABASE_ENCRYPTION_ENABLED:
        from .encryption import connect_encrypted_database

        conn = connect_encrypted_database(
            str(database),
            profile_id=profile_id,
            readonly=True,
        )
    else:
        path = Path(database).expanduser().resolve()
        conn = _driver.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn
