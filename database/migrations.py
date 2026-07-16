"""Migrations SQLite idempotentes exécutées au démarrage."""

from __future__ import annotations

import logging
import sqlite3

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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "mobile_device_id" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN mobile_device_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_mobile_device ON sessions(mobile_device_id)")


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_token_hash ON mobile_devices(token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_fcm_token ON mobile_devices(fcm_token)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mobile_pairing_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT UNIQUE NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_at DATETIME
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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_handles_apple ON imessage_handles(apple_handle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_handles_value ON imessage_handles(handle)")

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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_chats_apple ON imessage_chats(apple_chat_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_chat_handles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES imessage_chats(id),
            handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
            UNIQUE(chat_id, handle_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_ch_handle ON imessage_chat_handles(handle_id)")

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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_rowid ON imessage_messages(apple_rowid)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_guid ON imessage_messages(guid)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_hash ON imessage_messages(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_msg_chat ON imessage_messages(chat_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_msg_handle ON imessage_messages(handle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_msg_date ON imessage_messages(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_msg_associated ON imessage_messages(associated_message_guid)")

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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_att_apple ON imessage_attachments(apple_attachment_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_att_guid ON imessage_attachments(guid)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS imessage_message_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
            attachment_id INTEGER NOT NULL REFERENCES imessage_attachments(id),
            UNIQUE(message_id, attachment_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_ma_msg ON imessage_message_attachments(message_id)")

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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imessage_reactions_msg ON imessage_reactions(message_id)")

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
        "CREATE INDEX IF NOT EXISTS idx_perf_scope ON perf_benchmarks(scope, created_at DESC)")


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status)")


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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_rituals)").fetchall()}
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
        "CREATE INDEX IF NOT EXISTS idx_presence_arrived ON presence_sessions(arrived_at)")


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


def _migrate_conversations(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes enrichies à la table conversations (idempotent)."""
    migrations = [
        "ALTER TABLE conversations ADD COLUMN title TEXT",
        "ALTER TABLE conversations ADD COLUMN pinned BOOLEAN DEFAULT 0",
        "ALTER TABLE conversations ADD COLUMN archived BOOLEAN DEFAULT 0",
        "ALTER TABLE conversations ADD COLUMN tags TEXT",
        "ALTER TABLE conversations ADD COLUMN last_message_at DATETIME",
        "ALTER TABLE conversations ADD COLUMN message_count INTEGER DEFAULT 0",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass


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
        except Exception:
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


def _create_voice_debug_table(conn: sqlite3.Connection) -> None:
    """Crée la table voice_debug_log si elle n'existe pas (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS voice_debug_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
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


def _migrate_notification_deduplication_index(conn: sqlite3.Connection) -> None:
    """Ajoute l'index couvrant la recherche anti-doublon des notifications."""
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notif_dedup
        ON notifications(source, title, email_id, created_at DESC)
        """
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


def run_migrations(conn: sqlite3.Connection) -> None:
    """Applique dans un ordre stable toutes les migrations idempotentes."""
    _migrate_people_ai_description(conn)
    _migrate_people_imessage_count(conn)
    _migrate_people_timeline_cache(conn)
    _migrate_conversations(conn)
    _migrate_app_settings(conn)
    _migrate_email_summaries(conn)
    _migrate_message_insights(conn)
    _migrate_devagent(conn)
    _create_voice_debug_table(conn)
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
    _migrate_push_subscriptions(conn)
    _migrate_imessage_import(conn)
    _migrate_conversation_turns(conn)
    _migrate_memory_embeddings(conn)
    _migrate_notification_deduplication_index(conn)
    _migrate_mobile_chat_dedup(conn)
