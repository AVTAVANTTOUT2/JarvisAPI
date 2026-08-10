"""Migrations SQLite idempotentes exécutées au démarrage."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid

logger = logging.getLogger(__name__)


def _migrate_jarvis_journal(conn: sqlite3.Connection) -> None:
    """Journal quotidien écrit du point de vue de JARVIS (une entrée par jour)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jarvis_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            entry TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_day_scores(conn: sqlite3.Connection) -> None:
    """Scores quotidiens mis en cache : journée exceptionnelle, indice de chance."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            exceptional_score INTEGER,
            luck_score INTEGER,
            factors_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_sessions(conn: sqlite3.Connection) -> None:
    """Sessions d'authentification (verrouillage app). Un seul utilisateur, plusieurs devices.

    Le token brut n'est jamais stocké — seulement son hash SHA-256
    (`token_hash`), pour qu'une fuite de la base ne permette pas de rejouer
    une session active.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_agent TEXT,
            ip TEXT,
            revoked INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "mobile_device_id" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN mobile_device_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_mobile_device ON sessions(mobile_device_id)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_rate_limits (
            client_key TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            window_started_at TEXT NOT NULL,
            blocked_until TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_rate_limits_updated "
        "ON auth_rate_limits(updated_at)"
    )
    # L'ancien compteur global permettait à un client distant de verrouiller
    # tout JARVIS. Les clés sont retirées après création du stockage par client.
    conn.execute(
        """
        DELETE FROM app_settings
        WHERE key IN ('auth_failed_attempts', 'auth_lockout_until')
        """
    )


def _migrate_mobile_devices(conn: sqlite3.Connection) -> None:
    """Téléphones appairés, jetons natifs et codes de pairage éphémères."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mobile_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            model TEXT,
            token_hash TEXT UNIQUE,
            fcm_token TEXT,
            app_version TEXT,
            capabilities_json TEXT DEFAULT '{}',
            paired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            revoked INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_token_hash ON mobile_devices(token_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_fcm_token ON mobile_devices(fcm_token)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mobile_pairing_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT UNIQUE NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_at DATETIME
        )
    """)


def _migrate_remote_devices(conn: sqlite3.Connection) -> None:
    """Jetons hashés et pairage éphémère des ordinateurs distants."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
    if "token_hash" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN token_hash TEXT")
    if "revoked" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN revoked INTEGER DEFAULT 0")
    if "paired_at" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN paired_at DATETIME")
    if "token_rotated_at" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN token_rotated_at DATETIME")

    # Migration compatible avec les agents déjà installés : le jeton brut que
    # possède l'agent reste valable, mais sa copie en base est remplacée par
    # son empreinte SHA-256 puis effacée.
    if "auth_token" in columns:
        rows = conn.execute(
            """SELECT id, auth_token FROM devices
               WHERE auth_token IS NOT NULL AND auth_token != ''
                 AND token_hash IS NULL"""
        ).fetchall()
        for row in rows:
            token_hash = hashlib.sha256(str(row[1]).encode("utf-8")).hexdigest()
            conn.execute(
                """UPDATE devices
                   SET token_hash = ?, auth_token = NULL,
                       paired_at = COALESCE(paired_at, created_at)
                   WHERE id = ?""",
                (token_hash, row[0]),
            )
        conn.execute(
            "UPDATE devices SET auth_token = NULL WHERE auth_token IS NOT NULL"
        )

    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_token_hash
           ON devices(token_hash) WHERE token_hash IS NOT NULL"""
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_pairing_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT UNIQUE NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_at DATETIME
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_pairing_attempts (
            client_key TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            window_started_at DATETIME NOT NULL,
            blocked_until DATETIME
        )
    """)


def _migrate_push_subscriptions(conn: sqlite3.Connection) -> None:
    """Abonnements Web Push (un navigateur/device par ligne)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_imessage_import(conn: sqlite3.Connection) -> None:
    """Tables d'import des donnees brutes iMessage depuis chat.db.

    Cree les 8 tables (handles, chats, chat_handles, messages, attachments,
    message_attachments, reactions, sync_cursor) avec contraintes UNIQUE et index
    pour garantir la deduplication.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_handles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apple_handle_id INTEGER UNIQUE NOT NULL,
            handle TEXT NOT NULL,
            country TEXT,
            service TEXT DEFAULT 'iMessage',
            uncanonicalized_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_handles_apple ON imessage_handles(apple_handle_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_handles_value ON imessage_handles(handle)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apple_chat_id INTEGER UNIQUE NOT NULL,
            chat_identifier TEXT,
            display_name TEXT,
            group_id TEXT,
            style INTEGER DEFAULT 0,
            is_filtered INTEGER DEFAULT 0,
            last_message_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_chats_apple ON imessage_chats(apple_chat_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_chat_handles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES imessage_chats(id),
            handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
            UNIQUE(chat_id, handle_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_ch_handle ON imessage_chat_handles(handle_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apple_rowid INTEGER UNIQUE NOT NULL,
            guid TEXT UNIQUE NOT NULL,
            chat_id INTEGER REFERENCES imessage_chats(id),
            handle_id INTEGER REFERENCES imessage_handles(id),
            text TEXT,
            attributed_body BLOB,
            date INTEGER,
            date_read INTEGER,
            is_from_me INTEGER DEFAULT 0,
            is_read INTEGER DEFAULT 0,
            item_type INTEGER DEFAULT 0,
            group_title TEXT,
            associated_message_guid TEXT,
            associated_message_type INTEGER DEFAULT 0,
            content_hash TEXT UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_rowid ON imessage_messages(apple_rowid)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_guid ON imessage_messages(guid)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_hash ON imessage_messages(content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_msg_chat ON imessage_messages(chat_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_msg_handle ON imessage_messages(handle_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_msg_date ON imessage_messages(date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_msg_associated ON imessage_messages(associated_message_guid)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apple_attachment_id INTEGER UNIQUE NOT NULL,
            guid TEXT UNIQUE,
            filename TEXT,
            mime_type TEXT,
            transfer_name TEXT,
            total_bytes INTEGER,
            is_outgoing INTEGER DEFAULT 0,
            hide_attachment INTEGER DEFAULT 0,
            created_date INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_att_apple ON imessage_attachments(apple_attachment_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_att_guid ON imessage_attachments(guid)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_message_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
            attachment_id INTEGER NOT NULL REFERENCES imessage_attachments(id),
            UNIQUE(message_id, attachment_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_ma_msg ON imessage_message_attachments(message_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
            reactor_handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
            reaction_type INTEGER NOT NULL,
            apple_associated_guid TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(message_id, reactor_handle_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_reactions_msg ON imessage_reactions(message_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_sync_cursor (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            last_apple_rowid INTEGER DEFAULT 0,
            last_date INTEGER DEFAULT 0,
            last_guid TEXT,
            total_imported INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,
            started_at DATETIME,
            completed_at DATETIME,
            last_sync_at DATETIME,
            status TEXT DEFAULT 'idle' CHECK(status IN ('importing', 'idle', 'error')),
            error_message TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_consumer_cursors (
            consumer TEXT PRIMARY KEY,
            last_apple_rowid INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_conversation_turns(conn: sqlite3.Connection) -> None:
    """Tours de parole diarisés d'un enregistrement (mode écoute).

    `speaker_label` est un identifiant temporaire propre à CET enregistrement
    (« A », « B »…) — il n'est jamais réutilisé d'un enregistrement à l'autre
    (les labels de diarisation ne constituent pas une empreinte vocale persistante).
    `person_id` est renseigné après coup quand l'utilisateur répond
    « qui était la personne A ? ».
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            turn_order INTEGER NOT NULL,
            speaker_label TEXT NOT NULL,
            person_id INTEGER REFERENCES people(id),
            text TEXT NOT NULL,
            start_ms INTEGER,
            end_ms INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_turns_recording ON conversation_turns(recording_id)"
    )


def _migrate_memory_embeddings(conn: sqlite3.Connection) -> None:
    """Vecteurs d'embedding pour la recherche sémantique (episodes/recordings).

    `embedding` : vecteur float32 sérialisé (`numpy.tobytes()`). Le volume
    personnel (quelques milliers d'entrées au plus) rend une recherche par
    similarité cosinus en mémoire largement suffisante — pas besoin d'un
    moteur vectoriel dédié.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL CHECK(source_type IN ('recording', 'episode')),
            source_id INTEGER NOT NULL,
            text_preview TEXT,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_type, source_id)
        )
    """)


def _migrate_schema_migrations_table(conn: sqlite3.Connection) -> None:
    """Suivi des migrations SQLite versionnées appliquées (scripts/db_migrations.py)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            checksum TEXT NOT NULL,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_cursor_jobs_remove_merge_capability(conn: sqlite3.Connection) -> None:
    """Retire le flag de merge automatique abandonné sans perdre les jobs."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(cursor_delegation_jobs)").fetchall()
    }
    if "allow_merge" in columns:
        conn.execute("ALTER TABLE cursor_delegation_jobs DROP COLUMN allow_merge")


def _migrate_perf_benchmarks(conn: sqlite3.Connection) -> None:
    """Historique des temps d'exécution (suite de tests) — détection de régression."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS perf_benchmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            commit_sha TEXT,
            duration_ms REAL NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_perf_scope ON perf_benchmarks(scope, created_at DESC)"
    )


def _migrate_security_findings(conn: sqlite3.Connection) -> None:
    """Constats de l'audit sécurité (secrets exposés, patterns dangereux)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS security_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            rule TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('high', 'medium', 'low')),
            snippet TEXT,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'fixed', 'ignored')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file, line, rule)
        )
    """)


def _migrate_duplicate_findings(conn: sqlite3.Connection) -> None:
    """Blocs de code dupliqué détectés par le scanner périodique."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS duplicate_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_a TEXT NOT NULL, start_a INTEGER NOT NULL, end_a INTEGER NOT NULL,
            file_b TEXT NOT NULL, start_b INTEGER NOT NULL, end_b INTEGER NOT NULL,
            lines_count INTEGER NOT NULL,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'refactored', 'ignored')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_a, start_a, file_b, start_b)
        )
    """)


def _migrate_running_gags(conn: sqlite3.Connection) -> None:
    """Colonne people.running_gags — liste JSON des blagues récurrentes par contact."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    if "running_gags" not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN running_gags TEXT")


def _migrate_commitments(conn: sqlite3.Connection) -> None:
    """Engagements pris par l'utilisateur (« je t'envoie ça demain »)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS commitments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            made_to TEXT,
            due_hint TEXT,
            source TEXT DEFAULT 'conversation',
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'kept', 'dropped')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status)"
    )


def _migrate_daily_rituals(conn: sqlite3.Connection) -> None:
    """Table des rituels quotidiens : roast, debrief, citation, score (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_rituals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            roast TEXT,
            debrief TEXT,
            quote TEXT,
            productivity_score INTEGER,
            score_detail TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(daily_rituals)").fetchall()
    }
    if "weekly_debrief" not in cols:
        conn.execute("ALTER TABLE daily_rituals ADD COLUMN weekly_debrief TEXT")


def _migrate_mood_signals(conn: sqlite3.Connection) -> None:
    """Signaux comportementaux quotidiens (aucun diagnostic, juste des chiffres)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mood_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            msg_count INTEGER DEFAULT 0,
            msg_avg_14d REAL DEFAULT 0,
            deviation_pct REAL,
            voice_count INTEGER DEFAULT 0,
            screen_minutes REAL DEFAULT 0,
            late_night_points INTEGER DEFAULT 0,
            flags TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migrate_presence_sessions(conn: sqlite3.Connection) -> None:
    """Sessions de présence au bureau (détection par le son)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS presence_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arrived_at DATETIME NOT NULL,
            left_at DATETIME,
            duration_min REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_presence_arrived ON presence_sessions(arrived_at)"
    )


def _migrate_people_birthday(conn: sqlite3.Connection) -> None:
    """Ajoute people.birthday ('YYYY-MM-DD' ou 'MM-DD') aux bases existantes."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    if "birthday" not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN birthday TEXT")


def _migrate_messages_fts(conn: sqlite3.Connection) -> None:
    """Index plein-texte FTS5 sur messages.content (idempotent).

    Table externe (content='messages') synchronisée par triggers, backfill
    automatique si l'index est désynchronisé (base existante, restauration…).
    Si SQLite est compilé sans FTS5, la recherche retombe sur LIKE.
    """
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content='messages', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
        """)
    except sqlite3.OperationalError as e:
        logger.warning("[DB] FTS5 indisponible (%s) — recherche en LIKE", e)
        return
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
    """)
    n_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    n_fts = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    if n_fts != n_msg:
        logger.info("[DB] Rebuild index FTS (%d messages, index=%d)", n_msg, n_fts)
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")


def _fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    return row is not None


def _fts_query(query: str) -> str:
    """Transforme une saisie libre en requête FTS5 sûre.

    Chaque mot est mis entre guillemets (neutralise les opérateurs AND/OR/NEAR
    et la ponctuation), le dernier mot est en préfixe pour la recherche
    au fil de la saisie.
    """
    tokens = [t.replace('"', "") for t in query.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    quoted = [f'"{t}"' for t in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


def _migrate_people_ai_description(conn: sqlite3.Connection) -> None:
    """Ajoute la colonne ai_description aux bases déjà créées sans elle."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    if "ai_description" not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN ai_description TEXT")


def _migrate_people_imessage_count(conn: sqlite3.Connection) -> None:
    """Ajoute la colonne imessage_count pour stocker le nombre de messages iMessage analysés."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    if "imessage_count" not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN imessage_count INTEGER DEFAULT 0")


def _migrate_people_timeline_cache(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes de cache timeline à la table people."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    if "timeline_cache" not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN timeline_cache TEXT")
    if "timeline_updated_at" not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN timeline_updated_at DATETIME")


def _migrate_app_settings(conn: sqlite3.Connection) -> None:
    """Crée la table app_settings si elle n'existe pas encore (bases antérieures)."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )


def _migrate_local_activity_timestamps_to_utc(conn: sqlite3.Connection) -> None:
    """Convertit une fois les anciens instants locaux naïfs en UTC SQLite.

    Jusqu'à cette migration, la localisation et la présence écrivaient avec
    ``datetime.now()`` tandis que le reste du schéma reposait principalement
    sur ``CURRENT_TIMESTAMP`` (UTC). Les valeurs avec offset reçues des clients
    sont elles aussi canonicalisées. Le marqueur rend la transformation
    transactionnelle et strictement idempotente.
    """
    marker = "timestamp_storage_utc_v1"
    if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (marker,)).fetchone():
        return

    from database.time_buckets import sqlite_utc_timestamp

    timestamp_columns = {
        "location_history": ("created_at",),
        "visits": ("arrived_at", "departed_at"),
        "trips": ("started_at", "ended_at"),
        "presence_sessions": ("arrived_at", "left_at"),
        "places": ("last_visit",),
    }
    for table, columns in timestamp_columns.items():
        existing_columns = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for column in columns:
            if column not in existing_columns:
                continue
            rows = conn.execute(
                f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
            for rowid, raw_value in rows:
                try:
                    canonical = sqlite_utc_timestamp(str(raw_value))
                except (TypeError, ValueError):
                    logger.warning(
                        "Timestamp historique invalide ignoré: %s.%s rowid=%s",
                        table,
                        column,
                        rowid,
                    )
                    continue
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (canonical, rowid),
                )

    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, CURRENT_TIMESTAMP)",
        (marker,),
    )


def _migrate_application_timestamps_to_utc_v2(conn: sqlite3.Connection) -> None:
    """Canonicalise les écrivains applicatifs historiquement locaux.

    Ces colonnes étaient toujours alimentées par ``datetime.now()`` naïf ou
    ``datetime('now', 'localtime')``. Contrairement aux colonnes mixtes issues
    de sources externes, elles peuvent donc être converties sans heuristique.
    La migration s'exécute après la création de toutes les tables concernées.
    """
    marker = "timestamp_storage_utc_v2"
    if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (marker,)).fetchone():
        return

    from database.time_buckets import sqlite_utc_timestamp

    timestamp_columns = {
        "conversations": ("ended_at",),
        "cursor_delegation_jobs": (
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
        ),
        "food_suggestions": ("expires_at",),
        "fitness_session_progress": ("completed_at",),
        "fitness_prompt_log": ("prompted_at",),
    }
    for table, columns in timestamp_columns.items():
        existing_columns = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for column in columns:
            if column not in existing_columns:
                continue
            rows = conn.execute(
                f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
            for rowid, raw_value in rows:
                try:
                    canonical = sqlite_utc_timestamp(str(raw_value))
                except (TypeError, ValueError):
                    logger.warning(
                        "Timestamp applicatif invalide ignoré: %s.%s rowid=%s",
                        table,
                        column,
                        rowid,
                    )
                    continue
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (canonical, rowid),
                )

    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, CURRENT_TIMESTAMP)",
        (marker,),
    )


def _migrate_conversations(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes enrichies à la table conversations (idempotent)."""
    migrations = [
        "ALTER TABLE conversations ADD COLUMN title TEXT",
        "ALTER TABLE conversations ADD COLUMN pinned BOOLEAN DEFAULT 0",
        "ALTER TABLE conversations ADD COLUMN archived BOOLEAN DEFAULT 0",
        "ALTER TABLE conversations ADD COLUMN tags TEXT",
        "ALTER TABLE conversations ADD COLUMN last_message_at DATETIME",
        "ALTER TABLE conversations ADD COLUMN message_count INTEGER DEFAULT 0",
        "ALTER TABLE conversations ADD COLUMN checkpoint_id TEXT",
        "ALTER TABLE conversations ADD COLUMN title_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE conversations ADD COLUMN title_source TEXT",
        "ALTER TABLE conversations ADD COLUMN title_updated_at DATETIME",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass

    rows = conn.execute(
        "SELECT id FROM conversations WHERE checkpoint_id IS NULL OR checkpoint_id = ''"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE conversations SET checkpoint_id = ? WHERE id = ?",
            (str(uuid.uuid4()), row[0]),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_checkpoint_id "
        "ON conversations(checkpoint_id) WHERE checkpoint_id IS NOT NULL"
    )
    conn.execute(
        """
        UPDATE conversations
        SET title_status = 'manual',
            title_source = COALESCE(title_source, 'legacy'),
            title_updated_at = COALESCE(title_updated_at, last_message_at, started_at)
        WHERE title IS NOT NULL AND TRIM(title) != ''
          AND COALESCE(title_source, '') = ''
        """
    )


def _migrate_message_usage_estimation(conn: sqlite3.Connection) -> None:
    """Marque les comptages LLM estimés sans altérer les messages historiques."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "usage_estimated" not in columns:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN usage_estimated INTEGER NOT NULL "
            "DEFAULT 0 CHECK(usage_estimated IN (0, 1))"
        )


def _migrate_conversation_document_consent(conn: sqlite3.Connection) -> None:
    """Les documents historiques restent exclus du cloud par défaut."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(conversation_documents)").fetchall()
    }
    if "cloud_consent" not in columns:
        conn.execute(
            "ALTER TABLE conversation_documents "
            "ADD COLUMN cloud_consent BOOLEAN NOT NULL DEFAULT 0"
        )


def _migrate_email_summaries(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes de pré-traitement aux email_summaries (idempotent).

    Colonnes manquantes après le schema initial :
      - body (contenu intégral du mail)
      - received_at (date de réception brute)
      - category (urgent|finance|personnel|pro|newsletter|notification|info)
      - is_read (0 = non lu, 1 = lu)
      - created_at (horodatage INSERT du résumé, pour ORDER BY)
    """
    migrations = [
        "ALTER TABLE email_summaries ADD COLUMN body TEXT DEFAULT ''",
        "ALTER TABLE email_summaries ADD COLUMN received_at TEXT DEFAULT ''",
        "ALTER TABLE email_summaries ADD COLUMN category TEXT DEFAULT 'info'",
        "ALTER TABLE email_summaries ADD COLUMN is_read INTEGER DEFAULT 0",
        "ALTER TABLE email_summaries ADD COLUMN created_at TEXT DEFAULT ''",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


def _migrate_message_insights(conn: sqlite3.Connection) -> None:
    """Crée la table message_insights si elle n'existe pas (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            since_message_id INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            acknowledged INTEGER DEFAULT 0
        )
    """)


def _migrate_devagent(conn: sqlite3.Connection) -> None:
    """Cree les tables DevAgent autonome (idempotent)."""
    from database.devagent import migrate_devagent_tables

    migrate_devagent_tables(conn)


def _migrate_private_action_logs(conn: sqlite3.Connection) -> None:
    """Supprime une fois les anciens logs non rédigés.

    Les lignes historiques ont été écrites avant l'existence de la frontière
    de confidentialité ; elles ne peuvent pas être nettoyées de façon fiable
    a posteriori. Une purge unique est plus sûre qu'une pseudo-rédaction.
    """
    migration_key = "action_log_privacy_v1"
    applied = conn.execute(
        "SELECT 1 FROM app_settings WHERE key = ?",
        (migration_key,),
    ).fetchone()
    if applied:
        return
    conn.execute("DELETE FROM llm_action_logs")
    conn.execute("DELETE FROM dev_loop_log")
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, 'applied')",
        (migration_key,),
    )


def _create_voice_debug_table(conn: sqlite3.Connection) -> None:
    """Crée la table voice_debug_log si elle n'existe pas (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS voice_debug_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            input_text TEXT,
            system_prompt TEXT,
            messages_json TEXT,
            raw_response TEXT,
            response_clean TEXT,
            emotion TEXT,
            action_json TEXT,
            model TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            latency_stt_ms INTEGER DEFAULT 0,
            latency_llm1_ms INTEGER DEFAULT 0,
            latency_llm2_ms INTEGER DEFAULT 0,
            latency_tts_ms INTEGER DEFAULT 0,
            latency_total_ms INTEGER DEFAULT 0,
            stt_engine TEXT,
            tts_engine TEXT,
            audio_duration_ms INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vdebug_created ON voice_debug_log(created_at)"
    )


def _migrate_voice_debug_timestamps_to_utc(conn: sqlite3.Connection) -> None:
    """Convertit une fois les traces vocales locales historiques en UTC."""
    marker = "voice_debug_timestamp_utc_v1"
    if conn.execute(
        "SELECT 1 FROM app_settings WHERE key = ?", (marker,)
    ).fetchone():
        return

    from datetime import datetime, timezone

    from database.time_buckets import SQLITE_UTC_FORMAT, configured_timezone

    zone = configured_timezone()
    rows = conn.execute(
        "SELECT id, created_at FROM voice_debug_log WHERE created_at IS NOT NULL"
    ).fetchall()
    for trace_id, raw_value in rows:
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=zone)
            canonical = parsed.astimezone(timezone.utc).strftime(SQLITE_UTC_FORMAT)
        except (TypeError, ValueError):
            logger.warning(
                "Timestamp voice_debug_log invalide ignoré: id=%s", trace_id
            )
            continue
        conn.execute(
            "UPDATE voice_debug_log SET created_at = ? WHERE id = ?",
            (canonical, trace_id),
        )

    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, CURRENT_TIMESTAMP)",
        (marker,),
    )


def _migrate_notification_deduplication_index(conn: sqlite3.Connection) -> None:
    """Ajoute l'index couvrant la recherche anti-doublon des notifications."""
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notif_dedup
        ON notifications(source, title, email_id, created_at DESC)
        """
    )


def _migrate_location_point_dedup(conn: sqlite3.Connection) -> None:
    """Idempotence batch GPS mobile : (device_id, client_point_id) unique."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS location_point_dedup (
            device_id TEXT NOT NULL,
            client_point_id TEXT NOT NULL,
            location_history_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (device_id, client_point_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_location_point_dedup_created "
        "ON location_point_dedup(created_at)"
    )


def _migrate_mobile_chat_dedup(conn: sqlite3.Connection) -> None:
    """Idempotence des messages chat Android (device + client_message_id)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mobile_chat_dedup (
            device_id TEXT NOT NULL,
            client_message_id TEXT NOT NULL,
            conversation_id INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (device_id, client_message_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_chat_dedup_created "
        "ON mobile_chat_dedup(created_at)"
    )


def _migrate_fitness(conn: sqlite3.Connection) -> None:
    """Programme, suivi d'activité, nutrition, hydratation et bien-être."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(
                type IN ('poussee', 'tirage', 'jambes', 'full_body', 'natation', 'autre')
            ),
            exercises_json TEXT,
            duration_min INTEGER CHECK(duration_min IS NULL OR duration_min > 0),
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            meal_type TEXT CHECK(
                meal_type IS NULL OR
                meal_type IN ('petit_dej', 'dejeuner', 'diner', 'collation')
            ),
            description TEXT NOT NULL,
            calories_estimate INTEGER CHECK(
                calories_estimate IS NULL OR calories_estimate >= 0
            ),
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS water_intake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount_ml INTEGER NOT NULL CHECK(amount_ml > 0),
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS wellbeing_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            rating INTEGER CHECK(rating BETWEEN 1 AND 10),
            journal_text TEXT,
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(
                rating IS NOT NULL OR
                (journal_text IS NOT NULL AND length(trim(journal_text)) > 0)
            )
        );

        CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date);
        CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);
        CREATE INDEX IF NOT EXISTS idx_water_date ON water_intake(date);
        CREATE INDEX IF NOT EXISTS idx_wellbeing_date ON wellbeing_logs(date);

        CREATE TABLE IF NOT EXISTS fitness_programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            weekly_min_sessions INTEGER NOT NULL DEFAULT 3 CHECK(weekly_min_sessions BETWEEN 1 AND 7),
            calories_min INTEGER NOT NULL DEFAULT 3000 CHECK(calories_min >= 0),
            calories_max INTEGER NOT NULL DEFAULT 3500 CHECK(calories_max >= calories_min),
            protein_min_g INTEGER NOT NULL DEFAULT 120 CHECK(protein_min_g >= 0),
            protein_max_g INTEGER NOT NULL DEFAULT 145 CHECK(protein_max_g >= protein_min_g),
            reminders_enabled INTEGER NOT NULL DEFAULT 1 CHECK(reminders_enabled IN (0, 1)),
            reminder_time TEXT NOT NULL DEFAULT '18:00',
            reminder_interval_min INTEGER NOT NULL DEFAULT 120 CHECK(reminder_interval_min BETWEEN 30 AND 720),
            meal_tracking_enabled INTEGER NOT NULL DEFAULT 1 CHECK(meal_tracking_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS fitness_program_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id INTEGER NOT NULL REFERENCES fitness_programs(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
            type TEXT NOT NULL CHECK(
                type IN ('poussee', 'tirage', 'jambes', 'full_body', 'natation', 'autre')
            ),
            title TEXT NOT NULL,
            description TEXT,
            warmup_json TEXT NOT NULL DEFAULT '[]',
            exercises_json TEXT NOT NULL DEFAULT '[]',
            stretches_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(program_id, position)
        );

        CREATE TABLE IF NOT EXISTS fitness_session_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_session_id INTEGER NOT NULL REFERENCES fitness_program_sessions(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned', 'in_progress', 'done', 'skipped')),
            exercise_results_json TEXT NOT NULL DEFAULT '[]',
            duration_min INTEGER CHECK(duration_min IS NULL OR duration_min > 0),
            perceived_effort INTEGER CHECK(perceived_effort IS NULL OR perceived_effort BETWEEN 1 AND 10),
            notes TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(program_session_id, date)
        );

        CREATE TABLE IF NOT EXISTS fitness_weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            weight_kg REAL NOT NULL CHECK(weight_kg BETWEEN 20 AND 500),
            notes TEXT,
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS fitness_prompt_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('workout', 'meal')),
            reference TEXT NOT NULL,
            prompted_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(date, kind, reference, prompted_at)
        );

        CREATE INDEX IF NOT EXISTS idx_fitness_program_active ON fitness_programs(active);
        CREATE INDEX IF NOT EXISTS idx_fitness_sessions_day ON fitness_program_sessions(program_id, day_of_week);
        CREATE INDEX IF NOT EXISTS idx_fitness_progress_date ON fitness_session_progress(date, status);
        CREATE INDEX IF NOT EXISTS idx_fitness_weight_date ON fitness_weight_logs(date);
        CREATE INDEX IF NOT EXISTS idx_fitness_prompt_date ON fitness_prompt_log(date, kind);
        """
    )

    meal_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(meals)").fetchall()
    }
    if "protein_g" not in meal_columns:
        conn.execute(
            "ALTER TABLE meals ADD COLUMN protein_g REAL "
            "CHECK(protein_g IS NULL OR protein_g >= 0)"
        )
    meal_enrichments = (
        ("carbs_g", "REAL CHECK(carbs_g IS NULL OR carbs_g >= 0)"),
        ("fat_g", "REAL CHECK(fat_g IS NULL OR fat_g >= 0)"),
        ("fiber_g", "REAL CHECK(fiber_g IS NULL OR fiber_g >= 0)"),
        ("items_json", "TEXT"),
        ("photo_path", "TEXT"),
        (
            "analysis_source",
            (
                "TEXT NOT NULL DEFAULT 'manual' "
                "CHECK(analysis_source IN ('manual', 'text_ai', 'photo_ai'))"
            ),
        ),
        (
            "confidence",
            "REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1))",
        ),
        ("raw_input", "TEXT"),
    )
    meal_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(meals)").fetchall()
    }
    for column_name, column_ddl in meal_enrichments:
        if column_name not in meal_columns:
            conn.execute(f"ALTER TABLE meals ADD COLUMN {column_name} {column_ddl}")

    # Programme initial fourni par l'utilisateur. Les INSERT OR IGNORE le
    # rendent idempotent tout en laissant toutes les modifications ultérieures
    # intactes.
    conn.execute(
        """
        INSERT OR IGNORE INTO fitness_programs (
            id, name, goal, active, weekly_min_sessions,
            calories_min, calories_max, protein_min_g, protein_max_g,
            reminders_enabled, reminder_time, reminder_interval_min,
            meal_tracking_enabled
        ) VALUES (1, ?, ?, 1, 3, 3000, 3500, 120, 145, 1, '18:00', 120, 1)
        """,
        (
            "Programme poids du corps — prise de masse",
            "Prise de poids progressive avec 4 séances par semaine, 3 minimum, et natation occasionnelle.",
        ),
    )

    sessions = [
        {
            "position": 1,
            "day": 0,
            "type": "poussee",
            "title": "Poussée",
            "description": "Pectoraux, épaules et triceps.",
            "warmup": [
                {"name": "Cercles d'épaules", "duration_sec": 45},
                {"name": "Pompes scapulaires", "sets": 2, "reps": "10"},
                {"name": "Pompes inclinées faciles", "sets": 1, "reps": "10"},
            ],
            "exercises": [
                {
                    "name": "Pompes",
                    "sets": 4,
                    "reps": "8-15",
                    "progression": "Standard → pieds surélevés → tempo lent → sac à dos",
                },
                {
                    "name": "Pike push-ups",
                    "sets": 3,
                    "reps": "8-12",
                    "progression": "Surélever progressivement les pieds",
                },
                {
                    "name": "Dips entre deux chaises",
                    "sets": 3,
                    "reps": "8-12",
                    "progression": "Amplitude contrôlée, chaises parfaitement stables",
                },
                {
                    "name": "Pompes diamant",
                    "sets": 3,
                    "reps": "max propre",
                    "progression": "Arrêter avant la dégradation technique",
                },
                {
                    "name": "Planche",
                    "sets": 3,
                    "duration_sec": "30-60",
                    "progression": "Ajouter 5 à 10 secondes",
                },
            ],
            "stretches": [
                {
                    "name": "Étirement pectoral contre un mur",
                    "duration_sec": 30,
                    "sides": 2,
                },
                {
                    "name": "Triceps au-dessus de la tête",
                    "duration_sec": 30,
                    "sides": 2,
                },
                {"name": "Posture de l'enfant", "duration_sec": 45},
            ],
            "notes": "Dès que 12 à 15 répétitions sont propres, choisir une variante plus difficile, ralentir le tempo ou ajouter du volume.",
        },
        {
            "position": 2,
            "day": 1,
            "type": "tirage",
            "title": "Tirage avec barre",
            "description": "Dos, biceps, préhension et abdominaux.",
            "warmup": [
                {"name": "Cercles d'épaules et poignets", "duration_sec": 60},
                {"name": "Suspensions scapulaires légères", "sets": 2, "reps": "6-8"},
            ],
            "exercises": [
                {
                    "name": "Tractions pronation",
                    "sets": 4,
                    "reps": "max propre",
                    "progression": "Si nécessaire: 4×5 négatives de 3-5 s ou pied au sol; à 8-10 reps, ajouter du volume",
                },
                {
                    "name": "Tractions supination",
                    "sets": 3,
                    "reps": "max propre",
                    "progression": "Contrôler la descente",
                },
                {
                    "name": "Suspension active",
                    "sets": 3,
                    "duration_sec": "20-30",
                    "progression": "Ajouter 5 secondes",
                },
                {
                    "name": "Rows sous table",
                    "sets": 3,
                    "reps": "12-15",
                    "progression": "Avancer les pieds pour augmenter l'angle",
                },
                {
                    "name": "Relevés de jambes suspendu",
                    "sets": 3,
                    "reps": "10-15",
                    "progression": "Genoux fléchis puis jambes tendues",
                },
            ],
            "stretches": [
                {"name": "Étirement du grand dorsal", "duration_sec": 40, "sides": 2},
                {"name": "Avant-bras et poignets", "duration_sec": 30, "sides": 2},
                {"name": "Suspension passive douce", "duration_sec": 20},
            ],
            "notes": "La barre de traction est l'axe principal de progression du dos.",
        },
        {
            "position": 3,
            "day": 3,
            "type": "jambes",
            "title": "Jambes et fessiers",
            "description": "Force unilatérale, chaîne postérieure et mollets.",
            "warmup": [
                {"name": "Mobilité chevilles et hanches", "duration_sec": 90},
                {"name": "Squats contrôlés", "sets": 2, "reps": "10"},
            ],
            "exercises": [
                {
                    "name": "Squats bulgares ou pistol squat progressif",
                    "sets": 4,
                    "reps": "12-20",
                    "progression": "Réduire progressivement l'assistance",
                },
                {
                    "name": "Fentes marchées",
                    "sets": 3,
                    "reps": "12/jambe",
                    "progression": "Tempo 3 secondes en descente",
                },
                {
                    "name": "Hip thrust pied surélevé",
                    "sets": 3,
                    "reps": "15-20",
                    "progression": "Passer en unilatéral",
                },
                {
                    "name": "Mollets sur marche",
                    "sets": 4,
                    "reps": "20-25",
                    "progression": "Pause de 2 secondes en haut",
                },
                {
                    "name": "Wall sit",
                    "sets": 3,
                    "duration_sec": "30-45",
                    "progression": "Ajouter 5 secondes",
                },
            ],
            "stretches": [
                {"name": "Fléchisseurs de hanche", "duration_sec": 40, "sides": 2},
                {"name": "Ischio-jambiers", "duration_sec": 40, "sides": 2},
                {"name": "Mollets contre un mur", "duration_sec": 30, "sides": 2},
            ],
            "notes": "Éviter la natation juste avant cette séance.",
        },
        {
            "position": 4,
            "day": 4,
            "type": "full_body",
            "title": "Full body et renfort",
            "description": "Circuit complet ou travail ciblé des points faibles.",
            "warmup": [
                {"name": "Mobilité générale", "duration_sec": 120},
                {"name": "Montées de genoux légères", "duration_sec": 45},
            ],
            "exercises": [
                {
                    "name": "Pompes",
                    "sets": 4,
                    "reps": "8-15",
                    "progression": "Variante adaptée au niveau",
                },
                {
                    "name": "Squats",
                    "sets": 4,
                    "reps": "15-25",
                    "progression": "Tempo lent ou variante unilatérale",
                },
                {
                    "name": "Rows sous table",
                    "sets": 4,
                    "reps": "10-15",
                    "progression": "Augmenter l'inclinaison",
                },
                {
                    "name": "Fentes",
                    "sets": 3,
                    "reps": "12/jambe",
                    "progression": "Tempo contrôlé",
                },
                {
                    "name": "Gainage",
                    "sets": 3,
                    "duration_sec": "30-60",
                    "progression": "Variante plus difficile",
                },
            ],
            "stretches": [
                {"name": "Étirement global du dos", "duration_sec": 45},
                {"name": "Quadriceps", "duration_sec": 30, "sides": 2},
                {"name": "Pectoraux", "duration_sec": 30, "sides": 2},
            ],
            "notes": "Faire 3 à 5 tours du circuit, repos 90 secondes entre les tours, ou renforcer les points faibles de la semaine.",
        },
    ]
    for session in sessions:
        conn.execute(
            """
            INSERT OR IGNORE INTO fitness_program_sessions (
                program_id, position, day_of_week, type, title, description,
                warmup_json, exercises_json, stretches_json, notes
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["position"],
                session["day"],
                session["type"],
                session["title"],
                session["description"],
                json.dumps(session["warmup"], ensure_ascii=False),
                json.dumps(session["exercises"], ensure_ascii=False),
                json.dumps(session["stretches"], ensure_ascii=False),
                session["notes"],
            ),
        )


def _migrate_scheduler_job_runs(conn: sqlite3.Connection) -> None:
    """Historique des exécutions APScheduler pour la page /scheduler."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduler_job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            trigger TEXT NOT NULL DEFAULT 'cron'
                CHECK(trigger IN ('cron', 'manual')),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK(status IN ('running', 'ok', 'skipped', 'silent', 'error')),
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            duration_ms INTEGER,
            output TEXT,
            error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_job_started "
        "ON scheduler_job_runs(job_id, started_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_started "
        "ON scheduler_job_runs(started_at DESC)"
    )


def _migrate_food_orders(conn: sqlite3.Connection) -> None:
    """Journal des commandes de repas et garde-fou anti-double commande."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS food_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT,
            restaurant TEXT NOT NULL,
            items_json TEXT NOT NULL,
            total_price REAL CHECK(total_price IS NULL OR total_price >= 0),
            currency TEXT NOT NULL DEFAULT 'EUR',
            dry_run INTEGER NOT NULL DEFAULT 1 CHECK(dry_run IN (0, 1)),
            status TEXT NOT NULL CHECK(
                status IN ('planned', 'simulated', 'placed', 'blocked', 'failed')
            ),
            error TEXT,
            screenshot_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_food_orders_created
            ON food_orders(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_food_orders_status_created
            ON food_orders(status, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_food_orders_placed_plan
            ON food_orders(plan_id) WHERE status = 'placed' AND plan_id IS NOT NULL;
        """
    )


#: Colonnes de suivi de livraison ajoutées après la première version de la
#: table. `delivery_status` ne remplace pas `status` : l'un décrit l'issue de
#: la tentative côté JARVIS, l'autre l'avancement réel de la course.
_FOOD_ORDER_TRACKING_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "delivery_status",
        (
            "TEXT CHECK(delivery_status IS NULL OR delivery_status IN "
            "('placed', 'preparing', 'picked_up', 'on_the_way', 'delivered', 'cancelled'))"
        ),
    ),
    ("eta_minutes", "INTEGER CHECK(eta_minutes IS NULL OR eta_minutes >= 0)"),
    ("delivered_at", "DATETIME"),
    ("tracking_url", "TEXT"),
    ("rating", "INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5)"),
    ("suggestion_id", "INTEGER"),
)


def _migrate_food_intelligence(conn: sqlite3.Connection) -> None:
    """Suivi de livraison, notation, menus relevés, préférences et suggestions.

    La clé étrangère ``suggestion_id`` n'est pas déclarée en ``ALTER TABLE`` :
    SQLite ne sait pas ajouter une contrainte à une table existante sans la
    recréer, et recréer ``food_orders`` ferait perdre l'index unique partiel
    qui empêche la double commande. L'intégrité est donc tenue côté écriture.
    """
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(food_orders)").fetchall()
    }
    for name, ddl in _FOOD_ORDER_TRACKING_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE food_orders ADD COLUMN {name} {ddl}")

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_food_orders_delivery
            ON food_orders(delivery_status) WHERE delivery_status IS NOT NULL;

        CREATE TABLE IF NOT EXISTS food_menu_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant TEXT NOT NULL,
            item_name TEXT NOT NULL,
            category TEXT,
            price REAL CHECK(price IS NULL OR price >= 0),
            currency TEXT NOT NULL DEFAULT 'EUR',
            cuisine_type TEXT,
            available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0, 1)),
            scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(restaurant, item_name)
        );

        CREATE INDEX IF NOT EXISTS idx_food_menu_restaurant
            ON food_menu_cache(restaurant, available);

        CREATE TABLE IF NOT EXISTS food_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5
                CHECK(confidence >= 0.0 AND confidence <= 1.0),
            sample_size INTEGER NOT NULL DEFAULT 0 CHECK(sample_size >= 0),
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS food_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot INTEGER NOT NULL CHECK(slot >= 1),
            restaurant TEXT NOT NULL,
            items_json TEXT NOT NULL,
            estimated_price REAL CHECK(estimated_price IS NULL OR estimated_price >= 0),
            max_price REAL CHECK(max_price IS NULL OR max_price >= 0),
            currency TEXT NOT NULL DEFAULT 'EUR',
            reasoning TEXT,
            score REAL NOT NULL DEFAULT 0.0,
            factors_json TEXT,
            generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME,
            ordered INTEGER NOT NULL DEFAULT 0 CHECK(ordered IN (0, 1))
        );

        CREATE INDEX IF NOT EXISTS idx_food_suggestions_active
            ON food_suggestions(ordered, expires_at, slot);
        """
    )


def run_migrations(conn: sqlite3.Connection) -> None:
    """Applique dans un ordre stable toutes les migrations idempotentes."""
    _migrate_people_ai_description(conn)
    _migrate_people_imessage_count(conn)
    _migrate_people_timeline_cache(conn)
    _migrate_conversations(conn)
    _migrate_message_usage_estimation(conn)
    _migrate_conversation_document_consent(conn)
    _migrate_app_settings(conn)
    _migrate_local_activity_timestamps_to_utc(conn)
    _migrate_email_summaries(conn)
    _migrate_message_insights(conn)
    _migrate_devagent(conn)
    _migrate_cursor_jobs_remove_merge_capability(conn)
    _migrate_private_action_logs(conn)
    _create_voice_debug_table(conn)
    _migrate_voice_debug_timestamps_to_utc(conn)
    _migrate_messages_fts(conn)
    _migrate_daily_rituals(conn)
    _migrate_people_birthday(conn)
    _migrate_mood_signals(conn)
    _migrate_presence_sessions(conn)
    _migrate_running_gags(conn)
    _migrate_commitments(conn)
    _migrate_schema_migrations_table(conn)
    _migrate_perf_benchmarks(conn)
    _migrate_security_findings(conn)
    _migrate_duplicate_findings(conn)
    _migrate_jarvis_journal(conn)
    _migrate_day_scores(conn)
    _migrate_sessions(conn)
    _migrate_mobile_devices(conn)
    _migrate_remote_devices(conn)
    _migrate_push_subscriptions(conn)
    _migrate_imessage_import(conn)
    _migrate_conversation_turns(conn)
    _migrate_memory_embeddings(conn)
    _migrate_notification_deduplication_index(conn)
    _migrate_location_point_dedup(conn)
    _migrate_mobile_chat_dedup(conn)
    _migrate_fitness(conn)
    _migrate_scheduler_job_runs(conn)
    _migrate_food_orders(conn)
    _migrate_food_intelligence(conn)
    _migrate_application_timestamps_to_utc_v2(conn)
