"""SQLite database — schema complet JARVIS avec migrations automatiques."""

import logging
import sqlite3
import json
import math
import re
from pathlib import Path
from typing import Any
from datetime import datetime
from contextlib import contextmanager

import config

logger = logging.getLogger(__name__)

DB_PATH = Path(config.DB_PATH)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
-- ═══════════════════════════════════════════════════════════
-- MÉMOIRE ÉPISODIQUE
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    tags TEXT,                   -- JSON array
    embedding BLOB,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    agent TEXT,
    summary TEXT,
    mood_start INTEGER,
    mood_end INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER REFERENCES conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    agent TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════
-- LIFE COACH
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS life_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,       -- values, goals, fears, patterns, strengths
    content TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    relationship TEXT,
    personality_notes TEXT,
    dynamics TEXT,
    patterns TEXT,
    last_mentioned DATETIME,
    ai_description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS people_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
    event_type TEXT,
    content TEXT NOT NULL,
    lesson_learned TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mood_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mood_score INTEGER CHECK(mood_score BETWEEN 1 AND 10),
    energy_level INTEGER CHECK(energy_level BETWEEN 1 AND 10),
    context TEXT,
    triggers TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT,
    description TEXT NOT NULL,
    occurrences INTEGER DEFAULT 1,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'resolved', 'monitoring'))
);

-- ═══════════════════════════════════════════════════════════
-- ÉCOLE
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS school_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    teacher TEXT,
    schedule TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS school_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER REFERENCES school_subjects(id),
    title TEXT NOT NULL,
    content TEXT,
    doc_type TEXT,
    file_path TEXT,
    embedding BLOB,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS school_flashcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER REFERENCES school_subjects(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    next_review DATETIME DEFAULT CURRENT_TIMESTAMP,
    ease_factor REAL DEFAULT 2.5,
    interval_days INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════
-- PRODUCTIVITÉ
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('high', 'medium', 'low')),
    status TEXT DEFAULT 'todo' CHECK(status IN ('todo', 'doing', 'done')),
    due_date DATETIME,
    category TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS email_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_id TEXT UNIQUE,
    sender TEXT,
    subject TEXT,
    summary TEXT,
    action_needed BOOLEAN DEFAULT 0,
    priority TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE UNIQUE,
    morning_briefing TEXT,
    evening_summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════
-- NOTIFICATIONS (email watcher, alertes patterns, etc.)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,          -- email, pattern, calendar, system…
    title TEXT NOT NULL,
    content TEXT,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('urgent', 'high', 'medium', 'low')),
    read BOOLEAN DEFAULT 0,
    email_id TEXT,                 -- lien vers gmail_id si source=email
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    agent TEXT,
    action_type TEXT,
    payload TEXT,
    status TEXT CHECK(status IN ('success', 'error', 'pending')),
    execution_time_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_logs_created ON llm_action_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_logs_action_type ON llm_action_logs(action_type);

-- ═══════════════════════════════════════════════════════════
-- RÉGLAGES APPLICATIFS (dynamiques, sans redémarrage)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ═══════════════════════════════════════════════════════════
-- RÉSUMÉS HEBDO
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS weekly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start DATE,
    summary TEXT,
    patterns_spotted TEXT,
    recommendations TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════
-- INDEX
-- ═══════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent);
CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);
CREATE INDEX IF NOT EXISTS idx_mood_created ON mood_log(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_flashcards_review ON school_flashcards(next_review);
CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(read);
CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_email_summaries_gmail ON email_summaries(gmail_id);

-- ═══════════════════════════════════════════════════════════
-- MÉMOIRE PROFONDE
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS user_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'conversation',
    confidence TEXT DEFAULT 'medium',
    is_current BOOLEAN DEFAULT 1,
    superseded_by INTEGER REFERENCES user_facts(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_facts_category ON user_facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_current ON user_facts(is_current);

CREATE TABLE IF NOT EXISTS relationship_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
    handle TEXT,
    communication_style TEXT,
    response_pattern TEXT,
    topics TEXT,
    sentiment TEXT,
    power_dynamic TEXT,
    attachment_style TEXT,
    trust_level TEXT,
    interaction_frequency TEXT,
    last_analyzed DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_relprofile_person ON relationship_profiles(person_id);

CREATE TABLE IF NOT EXISTS relationship_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
    event_date DATE,
    event_type TEXT,
    summary TEXT NOT NULL,
    impact_on_user TEXT,
    lessons TEXT,
    source TEXT DEFAULT 'imessage',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_relevents_person ON relationship_events(person_id);
CREATE INDEX IF NOT EXISTS idx_relevents_date ON relationship_events(event_date);

CREATE TABLE IF NOT EXISTS cross_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_type TEXT,
    content TEXT NOT NULL,
    people_involved TEXT,
    evidence TEXT,
    actionable TEXT,
    occurrences INTEGER DEFAULT 1,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_crossinsights_type ON cross_insights(insight_type);

CREATE TABLE IF NOT EXISTS life_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start DATE,
    period_end DATE,
    context_type TEXT,
    description TEXT NOT NULL,
    impact_on_mood TEXT,
    impact_on_productivity TEXT,
    active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lifecontext_active ON life_context(active);

CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER REFERENCES conversations(id),
    label TEXT,
    title TEXT,
    duration_seconds INTEGER,
    transcription TEXT,
    summary TEXT,
    synthesis TEXT,
    actions_taken TEXT,
    audio_size_kb INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_recordings_date ON recordings(created_at);

-- ═══ LOCALISATION ═══

CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT CHECK(category IN (
        'home', 'school', 'work', 'gym', 'restaurant', 'shop',
        'friend', 'family', 'medical', 'transport', 'leisure', 'other'
    )),
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    radius_meters REAL DEFAULT 100,
    address TEXT,
    notes TEXT,
    visit_count INTEGER DEFAULT 0,
    avg_duration_min REAL,
    last_visit DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_places_name ON places(name);
CREATE INDEX IF NOT EXISTS idx_places_category ON places(category);

CREATE TABLE IF NOT EXISTS location_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    altitude REAL,
    accuracy REAL,
    speed REAL,
    heading REAL,
    source TEXT DEFAULT 'app',
    place_id INTEGER REFERENCES places(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_location_date ON location_history(created_at);
CREATE INDEX IF NOT EXISTS idx_location_place ON location_history(place_id);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL REFERENCES places(id),
    arrived_at DATETIME NOT NULL,
    departed_at DATETIME,
    duration_min REAL,
    day_of_week INTEGER,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_visits_place ON visits(place_id);
CREATE INDEX IF NOT EXISTS idx_visits_date ON visits(arrived_at);
CREATE INDEX IF NOT EXISTS idx_visits_day ON visits(day_of_week);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_place_id INTEGER REFERENCES places(id),
    to_place_id INTEGER REFERENCES places(id),
    started_at DATETIME,
    ended_at DATETIME,
    duration_min REAL,
    distance_km REAL,
    transport_mode TEXT,
    route_points TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trips_date ON trips(started_at);

CREATE TABLE IF NOT EXISTS location_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT CHECK(pattern_type IN (
        'routine', 'absence', 'new_place', 'frequency_change',
        'timing_change', 'unusual_visit', 'long_stay', 'short_stay'
    )),
    description TEXT NOT NULL,
    place_id INTEGER REFERENCES places(id),
    occurrences INTEGER DEFAULT 1,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'acknowledged', 'resolved'))
);

CREATE TABLE IF NOT EXISTS imessage_analysis_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    last_analyzed_rowid INTEGER DEFAULT 0,
    last_analyzed_at DATETIME,
    total_messages_analyzed INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imcache_handle ON imessage_analysis_cache(handle);

-- ═══ CONVERSATIONS ENRICHIES ═══

CREATE TABLE IF NOT EXISTS conversation_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT,
    file_size INTEGER,
    extracted_text TEXT,
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_convdocs_conv ON conversation_documents(conversation_id);

-- ═══════════════════════════════════════════════════════════
-- DAEMON JARVIS — ACTIVITÉ ÉCRAN, TEMPS APPS, MACHINES, SESSIONS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS screen_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL DEFAULT 'mac_mini',
    app TEXT,
    activity TEXT,
    mood TEXT CHECK(mood IN ('focused', 'idle', 'distracted', 'stuck', 'browsing', 'unknown')),
    notable TEXT,
    screenshot_hash TEXT,
    change_pct REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_screen_date ON screen_activity(created_at);
CREATE INDEX IF NOT EXISTS idx_screen_device ON screen_activity(device);
CREATE INDEX IF NOT EXISTS idx_screen_app ON screen_activity(app);

CREATE TABLE IF NOT EXISTS app_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL DEFAULT 'mac_mini',
    app TEXT NOT NULL,
    date DATE NOT NULL,
    duration_seconds INTEGER DEFAULT 0,
    session_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device, app, date)
);
CREATE INDEX IF NOT EXISTS idx_appusage_date ON app_usage(date);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT UNIQUE NOT NULL,
    device_name TEXT NOT NULL,
    device_type TEXT DEFAULT 'desktop',
    is_active BOOLEAN DEFAULT 0,
    is_online BOOLEAN DEFAULT 0,
    last_heartbeat DATETIME,
    last_screen_at DATETIME,
    ip_tailscale TEXT,
    auth_token TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_id ON devices(device_id);

CREATE TABLE IF NOT EXISTS work_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    app TEXT,
    started_at DATETIME NOT NULL,
    ended_at DATETIME,
    duration_min REAL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_worksessions_date ON work_sessions(started_at);

-- ═══════════════════════════════════════════════════════════
-- VOICE DEBUG — traces de pipeline vocal (STT + LLM + TTS)
-- ═══════════════════════════════════════════════════════════

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
);
CREATE INDEX IF NOT EXISTS idx_vdebug_created ON voice_debug_log(created_at);

-- ═══════════════════════════════════════════════════════════
-- WORKFLOWS AGENTIQUES (multi-étapes terminal complex)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS agentic_workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    user_message TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    final_synthesis TEXT,
    status TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed','partial')),
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    total_steps INTEGER DEFAULT 0,
    total_output_chars INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agentic_conv ON agentic_workflows(conversation_id);
CREATE INDEX IF NOT EXISTS idx_agentic_status ON agentic_workflows(status);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Crée toutes les tables si elles n'existent pas."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
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
    logger.info("[DB] Base initialisée : %s", DB_PATH)


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
            pass  # colonne déjà existante


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


# ── Helpers CRUD ────────────────────────────────────────────


def get_setting(key: str, default: str = "") -> str:
    """Lit un réglage applicatif depuis `app_settings`. Retourne `default` si absent."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    """Écrit ou met à jour un réglage applicatif dans `app_settings`."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def save_message(conversation_id: int, role: str, content: str,
                 agent: str = None, model: str = None,
                 tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, agent, model, tokens_in, tokens_out, cost)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, role, content, agent, model, tokens_in, tokens_out, cost)
        )
        return cur.lastrowid


def create_agentic_workflow(
    conversation_id: int,
    user_message: str,
    initial_action: dict,
) -> int:
    """Crée un workflow agentique en cours."""
    payload = json.dumps(
        [{"step": 0, "action": initial_action}],
        ensure_ascii=False,
        default=str,
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO agentic_workflows
               (conversation_id, user_message, steps_json, status, total_steps, total_output_chars)
               VALUES (?, ?, ?, 'running', 0, 0)""",
            (conversation_id, user_message, payload),
        )
        return int(cur.lastrowid)


def update_agentic_workflow(
    workflow_id: int,
    *,
    steps_json: str,
    status: str,
    final_synthesis: str | None = None,
    total_steps: int = 0,
    total_output_chars: int = 0,
) -> None:
    """Met à jour un workflow agentique à la fin (ou en échec)."""
    with get_db() as conn:
        conn.execute(
            """UPDATE agentic_workflows
               SET steps_json = ?, status = ?, final_synthesis = ?,
                   total_steps = ?, total_output_chars = ?,
                   completed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                steps_json,
                status,
                final_synthesis,
                total_steps,
                total_output_chars,
                workflow_id,
            ),
        )


def create_conversation(agent: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (agent) VALUES (?)", (agent,)
        )
        return cur.lastrowid


def end_conversation(conv_id: int, summary: str = None):
    with get_db() as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ?, summary = ? WHERE id = ?",
            (datetime.now().isoformat(), summary, conv_id)
        )


# ── Helpers conversations enrichies ─────────────────────────

def get_conversations(limit: int = 50, archived: bool = False) -> list[dict]:
    """Liste des conversations triées par dernière activité."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.*,
                (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message,
                (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count
            FROM conversations c
            WHERE COALESCE(c.archived, 0) = ?
            ORDER BY COALESCE(c.last_message_at, c.started_at) DESC
            LIMIT ?
        """, (1 if archived else 0, limit)).fetchall()
        return [dict(r) for r in rows]


def get_conversation_detail(conv_id: int) -> dict | None:
    """Retourne la conversation avec ses messages et documents."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["messages"] = get_conversation_history(conv_id, limit=200)
        docs = conn.execute(
            "SELECT id, original_name, file_type, file_size, summary, created_at FROM conversation_documents WHERE conversation_id = ?",
            (conv_id,)
        ).fetchall()
        result["documents"] = [dict(d) for d in docs]
        return result


def update_conversation(conv_id: int, **kwargs) -> None:
    """Met à jour un ou plusieurs champs de la conversation."""
    if not kwargs:
        return
    with get_db() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [conv_id]
        conn.execute(f"UPDATE conversations SET {sets} WHERE id = ?", vals)


def update_conversation_activity(conv_id: int) -> None:
    """Met à jour last_message_at et message_count après chaque message."""
    with get_db() as conn:
        conn.execute("""
            UPDATE conversations SET
                last_message_at = CURRENT_TIMESTAMP,
                message_count = (SELECT COUNT(*) FROM messages WHERE conversation_id = ?)
            WHERE id = ?
        """, (conv_id, conv_id))


def delete_conversation(conv_id: int) -> None:
    """Supprime une conversation et tous ses messages + documents."""
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversation_documents WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))


def search_conversations(query: str, limit: int = 20) -> list[dict]:
    """Recherche dans les titres et le contenu des messages de toutes les conversations.

    Une conversation n'apparaît qu'une fois, avec son message correspondant le
    plus récent. Utilise l'index FTS5 (insensible aux accents, préfixe sur le
    dernier mot) quand il existe, sinon LIKE.
    """
    q = (query or "").strip()
    if not q:
        return []
    with get_db() as conn:
        rows: list = []
        fts_q = _fts_query(q)
        if fts_q and _fts_available(conn):
            try:
                rows = conn.execute("""
                    SELECT c.id, c.title, c.started_at, c.last_message_at, c.message_count,
                           m.content AS matching_message, MAX(m.created_at) AS match_date
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.rowid
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE messages_fts MATCH ?
                    GROUP BY c.id
                    ORDER BY match_date DESC
                    LIMIT ?
                """, (fts_q, limit)).fetchall()
            except sqlite3.OperationalError as e:
                logger.warning("search_conversations FTS (%s) — fallback LIKE", e)
                rows = []
            if rows:
                # L'index FTS ne couvre que le contenu — ajoute les matchs de titre.
                seen = {r["id"] for r in rows}
                title_rows = conn.execute("""
                    SELECT c.id, c.title, c.started_at, c.last_message_at, c.message_count,
                           NULL AS matching_message, c.last_message_at AS match_date
                    FROM conversations c
                    WHERE c.title LIKE ?
                    ORDER BY c.last_message_at DESC
                    LIMIT ?
                """, (f"%{q}%", limit)).fetchall()
                rows = list(rows) + [r for r in title_rows if r["id"] not in seen]
                rows = rows[:limit]
        if not rows:
            rows = conn.execute("""
                SELECT c.id, c.title, c.started_at, c.last_message_at, c.message_count,
                       m.content AS matching_message, MAX(m.created_at) AS match_date
                FROM conversations c
                JOIN messages m ON m.conversation_id = c.id
                WHERE m.content LIKE ? OR c.title LIKE ?
                GROUP BY c.id
                ORDER BY match_date DESC
                LIMIT ?
            """, (f"%{q}%", f"%{q}%", limit)).fetchall()
        return [dict(r) for r in rows]


def save_conversation_document(
    conv_id: int,
    filename: str,
    original_name: str,
    file_path: str,
    file_type: str,
    file_size: int,
    extracted_text: str | None = None,
    summary: str | None = None,
) -> int:
    """Enregistre un document attaché à une conversation."""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO conversation_documents
                (conversation_id, filename, original_name, file_path, file_type, file_size, extracted_text, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (conv_id, filename, original_name, file_path, file_type, file_size, extracted_text, summary))
        return cur.lastrowid


def get_conversation_documents(conv_id: int) -> list[dict]:
    """Retourne tous les documents attachés à une conversation."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_documents WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def save_episode(agent: str, content: str, summary: str = None,
                 importance: int = 5, tags: list = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO episodes (agent, content, summary, importance, tags)
               VALUES (?, ?, ?, ?, ?)""",
            (agent, content, summary, importance, json.dumps(tags or []))
        )
        return cur.lastrowid


def save_recording(
    conversation_id: int | None,
    label: str,
    duration_seconds: int,
    transcription: str,
    summary: str,
    synthesis: dict,
    actions: dict,
    audio_size_kb: int,
    title: str | None = None,
) -> int:
    """Persiste un enregistrement continu (transcription + synthèse + actions)."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO recordings (conversation_id, label, title, duration_seconds, transcription, summary, synthesis, actions_taken, audio_size_kb)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                label,
                title,
                duration_seconds,
                transcription,
                summary,
                json.dumps(synthesis, ensure_ascii=False) if isinstance(synthesis, dict) else (synthesis or ""),
                json.dumps(actions, ensure_ascii=False) if isinstance(actions, dict) else (actions or ""),
                audio_size_kb,
            ),
        )
        return cur.lastrowid


def get_recordings(limit: int = 20) -> list:
    """Liste légère (pas de transcription complète dans les lignes — colonne summary uniquement)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, label, title, duration_seconds, summary, actions_taken, created_at, audio_size_kb
               FROM recordings ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        acts: dict = {}
        raw = d.get("actions_taken")
        if raw and isinstance(raw, str):
            try:
                acts = json.loads(raw)
            except json.JSONDecodeError:
                pass
        d["tasks_created"] = int(acts.get("tasks_created", 0))
        d["events_created"] = int(acts.get("events_created", 0))
        d["facts_stored"] = int(acts.get("facts_stored", 0))
        d["people_updated"] = int(acts.get("people_updated", 0))
        d.pop("actions_taken", None)
        out.append(d)
    return out


def get_recording(recording_id: int) -> dict | None:
    """Détail complet, y compris transcription et JSONs parsés."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("synthesis", "actions_taken"):
        v = d.get(k)
        if v and isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except json.JSONDecodeError:
                d[k] = None
    return d


def get_recent_episodes(agent: str = None, limit: int = 10) -> list:
    with get_db() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
                (agent, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_life_profile() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT category, content FROM life_profile ORDER BY category").fetchall()
        profile = {}
        for r in rows:
            cat = r["category"]
            if cat not in profile:
                profile[cat] = []
            profile[cat].append(r["content"])
        return profile


def get_person(name: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM people WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
        if row:
            result = dict(row)
            # people_events (legacy)
            pevents = conn.execute(
                "SELECT *, content as content, '' as summary FROM people_events WHERE person_id = ? ORDER BY created_at DESC LIMIT 10",
                (row["id"],)
            ).fetchall()
            # relationship_events (mémoire profonde, source principale)
            revents = conn.execute(
                "SELECT *, '' as content, summary FROM relationship_events WHERE person_id = ? ORDER BY event_date DESC, created_at DESC LIMIT 15",
                (row["id"],)
            ).fetchall()
            # Merge : relationship_events en premier (plus riches), puis people_events
            all_events: list[dict] = [dict(e) for e in revents]
            for pe in pevents:
                all_events.append(dict(pe))
            result["events"] = all_events[:15]
            return result
        return None


def get_all_people() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM people ORDER BY last_mentioned DESC").fetchall()
        return [dict(r) for r in rows]


def get_people_sorted_by_recent() -> list:
    """Contacts triés par dernière interaction (last_mentioned, puis événements, puis created_at).
    
    Inclut message_count = imessage_count (messages iMessage analysés) ou fallback sur events.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                COALESCE(
                    NULLIF(p.imessage_count, 0),
                    (SELECT COUNT(*) FROM people_events WHERE person_id = p.id) +
                    (SELECT COUNT(*) FROM relationship_events WHERE person_id = p.id)
                ) as message_count
            FROM people p
            ORDER BY datetime(
                COALESCE(
                    NULLIF(TRIM(p.last_mentioned), ''),
                    (SELECT MAX(created_at) FROM people_events e WHERE e.person_id = p.id),
                    (SELECT MAX(created_at) FROM relationship_events r WHERE r.person_id = p.id),
                    p.created_at
                )
            ) DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def set_person_ai_description(person_id: int, text: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE people SET ai_description = ? WHERE id = ?",
            (text, person_id),
        )


def clear_person_ai_description(person_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE people SET ai_description = NULL WHERE id = ?", (person_id,))


def upsert_person(name: str, **kwargs) -> int:
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM people WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [existing["id"]]
            conn.execute(f"UPDATE people SET {sets}, last_mentioned = CURRENT_TIMESTAMP WHERE id = ?", vals)
            return existing["id"]
        else:
            cols = ", ".join(["name"] + list(kwargs.keys()))
            placeholders = ", ".join(["?"] * (1 + len(kwargs)))
            vals = [name] + list(kwargs.values())
            cur = conn.execute(f"INSERT INTO people ({cols}) VALUES ({placeholders})", vals)
            return cur.lastrowid


def update_person_imessage_count(person_id: int, count: int) -> None:
    """Met à jour le compteur de messages iMessage analysés pour un contact."""
    with get_db() as conn:
        conn.execute(
            "UPDATE people SET imessage_count = ? WHERE id = ?",
            (count, person_id),
        )


def rename_person_if_phone_number(person_id: int, new_name: str) -> bool:
    """Renomme un contact si son nom actuel est un numéro de téléphone.
    
    Returns True si renommé, False sinon.
    """
    import re
    with get_db() as conn:
        row = conn.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            return False
        current_name = row["name"] or ""
        # Vérifie si le nom actuel est un numéro de téléphone
        if re.match(r'^[\+\d\s\-\.]+$', current_name.strip()):
            # Vérifie qu'un contact avec ce nom n'existe pas déjà
            existing = conn.execute(
                "SELECT id FROM people WHERE LOWER(name) = LOWER(?) AND id != ?",
                (new_name, person_id)
            ).fetchone()
            if not existing:
                conn.execute(
                    "UPDATE people SET name = ? WHERE id = ?",
                    (new_name, person_id),
                )
                return True
    return False


def get_person_timeline_cache(name: str) -> dict | None:
    """Retourne le cache timeline d'un contact (timeline_cache JSON + timeline_updated_at).

    Retourne None si le contact n'existe pas ou si le cache est vide.
    Retourne un dict {"events": [...], "updated_at": "ISO datetime string"}.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT timeline_cache, timeline_updated_at FROM people WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()
    if not row or not row["timeline_cache"]:
        return None
    try:
        import json as _json
        events = _json.loads(row["timeline_cache"])
        return {"events": events, "updated_at": row["timeline_updated_at"]}
    except Exception:
        return None


def update_person_timeline_cache(name: str, events: list) -> None:
    """Sérialise `events` en JSON et l'enregistre dans people.timeline_cache.

    Met à jour timeline_updated_at au timestamp courant (UTC).
    """
    import json as _json
    payload = _json.dumps(events, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """UPDATE people
               SET timeline_cache = ?,
                   timeline_updated_at = CURRENT_TIMESTAMP
               WHERE LOWER(name) = LOWER(?)""",
            (payload, name),
        )


def patch_person(old_name: str, fields: dict[str, Any]) -> dict | None:
    """Met à jour une ligne `people` identifiée par le nom (insensible à la casse).

    Champs autorisés : name, relationship, personality_notes, dynamics,
    patterns, birthday. Lève ``ValueError`` si le nouveau nom est déjà
    utilisé par un autre contact.
    """
    allowed = ("name", "relationship", "personality_notes", "dynamics", "patterns", "birthday")
    key = (old_name or "").strip()
    if not key:
        return None
    updates: dict[str, Any] = {}
    for k in allowed:
        if k not in fields:
            continue
        val = fields[k]
        if val is None:
            continue
        if k == "name":
            n = str(val).strip()
            if not n:
                continue
            updates[k] = n
        else:
            updates[k] = val if isinstance(val, str) else str(val)

    if not updates:
        return get_person(key)

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name FROM people WHERE LOWER(name) = LOWER(?)",
            (key,),
        ).fetchone()
        if not row:
            return None
        pid = row["id"]
        current_name = row["name"]

        if "name" in updates:
            new_n = str(updates["name"]).strip()
            conflict = conn.execute(
                "SELECT id FROM people WHERE LOWER(name) = LOWER(?) AND id != ?",
                (new_n, pid),
            ).fetchone()
            if conflict:
                raise ValueError(f"Une personne nommée « {new_n} » existe déjà.")

        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [pid]
        conn.execute(f"UPDATE people SET {sets} WHERE id = ?", vals)

    final_lookup = updates.get("name", current_name)
    return get_person(str(final_lookup))


# ── Life profile (CRUD) ─────────────────────────────────────

def add_life_profile_entry(category: str, content: str) -> int:
    """Ajoute une entrée au life profile. Retourne l'id."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO life_profile (category, content) VALUES (?, ?)",
            (category, content),
        )
        return cur.lastrowid


def update_life_profile_entry(entry_id: int, content: str) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE life_profile SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content, entry_id),
        )
        return cur.rowcount > 0


def delete_life_profile_entry(entry_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM life_profile WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def get_life_profile_entries() -> list:
    """Comme `get_life_profile()` mais retourne les ids (utile pour l'édition UI)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, category, content, updated_at FROM life_profile ORDER BY category, id"
        ).fetchall()
        return [dict(r) for r in rows]


# ── People events ───────────────────────────────────────────

def add_people_event(person_id_or_name, event_type: str, content: str,
                      lesson_learned: str = None) -> int | None:
    """Ajoute un event à une personne (résolue par id OU par nom).
    Crée la personne si elle n'existe pas (cas string)."""
    with get_db() as conn:
        if isinstance(person_id_or_name, int):
            person_id = person_id_or_name
        else:
            row = conn.execute(
                "SELECT id FROM people WHERE LOWER(name) = LOWER(?)",
                (person_id_or_name,),
            ).fetchone()
            if row:
                person_id = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO people (name, last_mentioned) VALUES (?, CURRENT_TIMESTAMP)",
                    (person_id_or_name,),
                )
                person_id = cur.lastrowid

        cur = conn.execute(
            """INSERT INTO people_events (person_id, event_type, content, lesson_learned)
               VALUES (?, ?, ?, ?)""",
            (person_id, event_type, content, lesson_learned),
        )
        return cur.lastrowid


# ── Patterns ────────────────────────────────────────────────

def create_pattern(pattern_type: str, description: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO patterns (pattern_type, description) VALUES (?, ?)",
            (pattern_type, description),
        )
        return cur.lastrowid


def update_pattern(pattern_id: int, occurrences_increment: int = 1) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE patterns
               SET occurrences = occurrences + ?, last_seen = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (occurrences_increment, pattern_id),
        )


def find_or_create_pattern(description: str, pattern_type: str = "behavioral") -> int:
    """Cherche un pattern par similarité simple (description identique). Sinon crée."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM patterns WHERE LOWER(description) = LOWER(?) AND status = 'active'",
            (description,),
        ).fetchone()
        if existing:
            update_pattern(existing["id"])
            return existing["id"]
        return create_pattern(pattern_type, description)


# ── Épisodes & résumés hebdomadaires ────────────────────────

def get_weekly_episodes(days: int = 7) -> list:
    """Épisodes des N derniers jours."""
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM episodes
                WHERE created_at >= datetime('now', '-{int(days)} days')
                ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def save_weekly_summary(week_start: str, summary: str,
                         patterns_spotted: list = None,
                         recommendations: list = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO weekly_summaries (week_start, summary, patterns_spotted, recommendations)
               VALUES (?, ?, ?, ?)""",
            (
                week_start, summary,
                json.dumps(patterns_spotted or []),
                json.dumps(recommendations or []),
            ),
        )
        return cur.lastrowid


def save_mood(mood: int, energy: int, context: str = None, triggers: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO mood_log (mood_score, energy_level, context, triggers) VALUES (?, ?, ?, ?)",
            (mood, energy, context, triggers)
        )
        return cur.lastrowid


def get_recent_moods(limit: int = 14) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM mood_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_patterns() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM patterns WHERE status = 'active' ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_tasks(status: str = None) -> list:
    """Liste les tâches. Par défaut : toutes celles non terminées (todo + doing).

    ``status`` : None (actives), ``"all"`` (toutes), ou ``"todo" | "doing" | "done"``.
    Tri intelligent : priorité (high < medium < low) puis date d'échéance.
    """
    priority_case = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
    with get_db() as conn:
        if status == "all":
            rows = conn.execute(
                f"SELECT * FROM tasks ORDER BY {priority_case}, due_date IS NULL, due_date"
            ).fetchall()
        elif status:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status = ? ORDER BY {priority_case}, due_date IS NULL, due_date",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status != 'done' "
                f"ORDER BY {priority_case}, due_date IS NULL, due_date"
            ).fetchall()
        return [dict(r) for r in rows]


def create_task(title: str, description: str = None, priority: str = "medium",
                due_date: str = None, category: str = None) -> int:
    """Crée une tâche. Retourne l'id."""
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (title, description, priority, due_date, category)
               VALUES (?, ?, ?, ?, ?)""",
            (title, description, priority, due_date, category),
        )
        return cur.lastrowid


def update_task_status(task_id: int, status: str) -> bool:
    """Met à jour le status d'une tâche. Si `done`, remplit `completed_at`."""
    if status not in ("todo", "doing", "done"):
        return False
    with get_db() as conn:
        if status == "done":
            cur = conn.execute(
                "UPDATE tasks SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, task_id),
            )
        else:
            cur = conn.execute(
                "UPDATE tasks SET status = ?, completed_at = NULL WHERE id = ?",
                (status, task_id),
            )
        return cur.rowcount > 0


def get_task(task_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def delete_task(task_id: int) -> bool:
    """Supprime une tâche par son ID. Retourne True si supprimée, False si absente."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


def delete_all_tasks() -> int:
    """Supprime TOUTES les tâches de la base. Retourne le nombre de lignes supprimées."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM tasks")
        return cur.rowcount


def get_daily_messages(date: str = None) -> list:
    """Récupère tous les messages d'une date (YYYY-MM-DD). Aujourd'hui par défaut.

    Utilisé par `productivity.evening_summary()` pour résumer la journée.
    """
    target = date or datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT role, content, agent, model, created_at
               FROM messages
               WHERE DATE(created_at) = ?
               ORDER BY created_at""",
            (target,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_daily_briefing(date: str, morning: str = None, evening: str = None) -> None:
    """Insert ou update le briefing du jour (UPSERT sur la date)."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, morning_briefing, evening_summary FROM daily_briefings WHERE date = ?",
            (date,),
        ).fetchone()
        if existing:
            new_m = morning if morning is not None else existing["morning_briefing"]
            new_e = evening if evening is not None else existing["evening_summary"]
            conn.execute(
                "UPDATE daily_briefings SET morning_briefing = ?, evening_summary = ? WHERE date = ?",
                (new_m, new_e, date),
            )
        else:
            conn.execute(
                "INSERT INTO daily_briefings (date, morning_briefing, evening_summary) VALUES (?, ?, ?)",
                (date, morning, evening),
            )


# ── École : documents de cours ──────────────────────────────

def save_school_document(title: str, content: str, doc_type: str = "cours",
                          file_path: str = None, subject_id: int = None) -> int:
    """Enregistre un document scolaire uploadé. Retourne l'id en DB."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO school_documents (subject_id, title, content, doc_type, file_path)
               VALUES (?, ?, ?, ?, ?)""",
            (subject_id, title, content, doc_type, file_path),
        )
        return cur.lastrowid


def get_school_documents(limit: int = 50) -> list:
    """Retourne les documents scolaires (sans le BLOB embedding)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, subject_id, title, doc_type, file_path,
                      LENGTH(COALESCE(content, '')) AS content_length,
                      created_at
               FROM school_documents
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation_history(conv_id: int, limit: int = 50) -> list:
    """Récupère les derniers messages d'une conversation, ordre chronologique ASC."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT role, content, agent, created_at
               FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Notifications ───────────────────────────────────────────

def create_notification(source: str, title: str, content: str = None,
                        priority: str = "medium", email_id: str = None) -> int:
    """Crée une notification. `priority` ∈ {urgent, high, medium, low}."""
    if priority not in ("urgent", "high", "medium", "low"):
        priority = "medium"
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO notifications (source, title, content, priority, email_id)
               VALUES (?, ?, ?, ?, ?)""",
            (source, title, content, priority, email_id),
        )
        return cur.lastrowid


def get_unread_notifications(limit: int = 50) -> list:
    """Notifications non lues, triées par priorité puis récence."""
    priority_order = (
        "CASE priority "
        "WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 ELSE 3 END"
    )
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM notifications
                WHERE read = 0
                ORDER BY {priority_order}, created_at DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_notifications(limit: int = 50) -> list:
    """Notifications récentes (lues + non lues)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_notification_read(notif_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,)
        )
        return cur.rowcount > 0


def mark_all_notifications_read() -> int:
    with get_db() as conn:
        cur = conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        return cur.rowcount


# ── LLM action logs ───────────────────────────────────────────

def log_llm_action(
    agent: str,
    action_type: str,
    payload: Any,
    status: str,
    execution_time_ms: int | None = None,
) -> int:
    """Persiste un log d'action LLM."""
    if status not in ("success", "error", "pending"):
        status = "pending"
    payload_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO llm_action_logs (agent, action_type, payload, status, execution_time_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent, action_type, payload_text, status, execution_time_ms),
        )
        return cur.lastrowid


def get_llm_logs(limit: int = 100, action_type: str | None = None) -> list[dict]:
    """Retourne les logs LLM les plus recents, optionnellement filtres par type.

    Si ``action_type`` vaut ``devagent`` ou commence par ``devagent_``, lit
    ``dev_loop_log``. Sans filtre, fusionne ``llm_action_logs`` et DevAgent.
    """
    from database.devagent import get_dev_loop_logs

    lim = max(1, min(int(limit), 1000))

    if action_type == "devagent" or (
        action_type and action_type.startswith("devagent_")
    ):
        phase = None
        if action_type and action_type.startswith("devagent_"):
            phase = action_type.removeprefix("devagent_")
        logs = get_dev_loop_logs(limit=lim)
        if phase:
            logs = [row for row in logs if row.get("action_type") == action_type]
        return logs[:lim]

    with get_db() as conn:
        if action_type:
            rows = conn.execute(
                """
                SELECT *
                FROM llm_action_logs
                WHERE action_type = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (action_type, lim),
            ).fetchall()
            return [dict(r) for r in rows]

        rows = conn.execute(
            """
            SELECT *
            FROM llm_action_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        llm_logs = [dict(r) for r in rows]

    dev_logs = get_dev_loop_logs(limit=lim)
    merged = llm_logs + dev_logs
    merged.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or 0), reverse=True)
    return merged[:lim]


# ── Email summaries ─────────────────────────────────────────

def upsert_email_summary(gmail_id: str, sender: str, subject: str,
                         summary: str, action_needed: bool = False,
                         priority: str = "medium") -> int:
    """Insert ou update un résumé d'email (UPSERT sur `gmail_id`)."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM email_summaries WHERE gmail_id = ?", (gmail_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE email_summaries
                   SET sender = ?, subject = ?, summary = ?,
                       action_needed = ?, priority = ?
                   WHERE gmail_id = ?""",
                (sender, subject, summary, 1 if action_needed else 0, priority, gmail_id),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO email_summaries
               (gmail_id, sender, subject, summary, action_needed, priority)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (gmail_id, sender, subject, summary, 1 if action_needed else 0, priority),
        )
        return cur.lastrowid


def get_recent_email_summaries(limit: int = 30, action_needed_only: bool = False) -> list:
    with get_db() as conn:
        if action_needed_only:
            rows = conn.execute(
                """SELECT * FROM email_summaries
                   WHERE action_needed = 1
                   ORDER BY processed_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM email_summaries ORDER BY processed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_processed_email_ids(limit: int = 200) -> set:
    """Retourne les `gmail_id` déjà analysés (pour init du watcher au démarrage)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT gmail_id FROM email_summaries ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {r["gmail_id"] for r in rows if r["gmail_id"]}


def get_all_processed_email_ids() -> set[str]:
    """Tous les `gmail_id` déjà présents dans `email_summaries` (dédupage fiable après long arrêt)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT gmail_id FROM email_summaries WHERE gmail_id IS NOT NULL AND TRIM(gmail_id) != ''"
        ).fetchall()
        return {str(r["gmail_id"]).strip() for r in rows if r["gmail_id"]}


def save_email_full(
    gmail_id: str,
    sender: str,
    subject: str,
    body: str,
    received_at: str,
    summary: str,
    category: str = "info",
    priority: str = "low",
) -> int:
    """Sauvegarde un email pré-traité avec contenu intégral + résumé DeepSeek.

    UPSERT sur ``gmail_id`` : si l'email est déjà en base, on met à jour
    les champs (sender, subject, body, summary, category, priority).
    ``is_read`` reste à ``0`` (l'email n'a pas encore été lu via JARVIS).
    ``created_at`` est mis à jour au timestamp courant.
    """
    now_iso = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM email_summaries WHERE gmail_id = ?", (gmail_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE email_summaries SET
                       sender = ?, subject = ?, body = ?, received_at = ?,
                       summary = ?, category = ?, priority = ?,
                       created_at = ?
                   WHERE gmail_id = ?""",
                (sender, subject, body, received_at, summary, category, priority, now_iso, gmail_id),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO email_summaries
               (gmail_id, sender, subject, body, received_at, summary, category, priority, is_read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (gmail_id, sender, subject, body, received_at, summary, category, priority, now_iso),
        )
        return cur.lastrowid


def get_unread_emails_from_db(limit: int = 20) -> list[dict]:
    """Récupère les emails non lus depuis la DB (instantané, pas d'AppleScript).

    Retourne une liste de dicts avec les champs :
      gmail_id, sender, subject, body, received_at, summary, category, priority.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT gmail_id, sender, subject, body, received_at, summary, category, priority
               FROM email_summaries
               WHERE is_read = 0
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_emails_from_db(limit: int = 20, category: str | None = None) -> list[dict]:
    """Récupère les emails récents (lus ou non) depuis la DB.

    Args:
        limit: Nombre maximum d'emails à retourner.
        category: Filtre optionnel par catégorie (ex: "urgent", "finance").
    """
    with get_db() as conn:
        if category:
            rows = conn.execute(
                """SELECT gmail_id, sender, subject, body, received_at, summary, category, priority, is_read
                   FROM email_summaries
                   WHERE category = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT gmail_id, sender, subject, body, received_at, summary, category, priority, is_read
                   FROM email_summaries
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def mark_email_read(gmail_id: str) -> None:
    """Marque un email comme lu en DB."""
    with get_db() as conn:
        conn.execute(
            "UPDATE email_summaries SET is_read = 1 WHERE gmail_id = ?",
            (gmail_id,),
        )


def get_email_stats() -> dict:
    """Stats rapides : total, non lus, urgents."""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM email_summaries").fetchone()[0]
        unread = conn.execute("SELECT COUNT(*) FROM email_summaries WHERE is_read = 0").fetchone()[0]
        urgent = conn.execute(
            "SELECT COUNT(*) FROM email_summaries WHERE is_read = 0 AND priority = 'high'"
        ).fetchone()[0]
    return {"total": total, "unread": unread, "urgent": urgent}


# ── User Facts (mémoire profonde) ─────────────────────────────

def add_fact(category: str, content: str, source: str = "conversation",
             confidence: str = "medium") -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO user_facts (category, content, source, confidence)
               VALUES (?, ?, ?, ?)""",
            (category, content, source, confidence),
        )
        return cur.lastrowid


def get_facts(category: str = None, current_only: bool = True) -> list:
    with get_db() as conn:
        clauses = []
        params: list = []
        if current_only:
            clauses.append("is_current = 1")
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM user_facts {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_facts_summary() -> dict:
    """Retourne {category: [facts]} pour injection dans le contexte Sonnet."""
    facts = get_facts(current_only=True)
    summary: dict[str, list] = {}
    for f in facts:
        cat = f["category"]
        if cat not in summary:
            summary[cat] = []
        summary[cat].append(f)
    return summary


def invalidate_fact(fact_id: int, superseded_by: int = None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE user_facts SET is_current = 0, superseded_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (superseded_by, fact_id),
        )


def search_facts(query: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM user_facts WHERE is_current = 1 AND content LIKE ? ORDER BY updated_at DESC",
            (f"%{query}%",),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Relationship Profiles ─────────────────────────────────────

def upsert_relationship_profile(person_id: int, **kwargs) -> int:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM relationship_profiles WHERE person_id = ?", (person_id,)
        ).fetchone()
        if existing:
            if kwargs:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                vals = list(kwargs.values()) + [existing["id"]]
                conn.execute(
                    f"UPDATE relationship_profiles SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    vals,
                )
            return existing["id"]
        cols = ["person_id"] + list(kwargs.keys())
        placeholders = ", ".join(["?"] * len(cols))
        vals = [person_id] + list(kwargs.values())
        cur = conn.execute(
            f"INSERT INTO relationship_profiles ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        return cur.lastrowid


def get_relationship_profile(person_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT rp.*, p.name, p.relationship
               FROM relationship_profiles rp
               JOIN people p ON p.id = rp.person_id
               WHERE rp.person_id = ?""",
            (person_id,),
        ).fetchone()
        return dict(row) if row else None


def get_all_relationship_profiles() -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT rp.*, p.name, p.relationship, p.dynamics, p.personality_notes
               FROM relationship_profiles rp
               JOIN people p ON p.id = rp.person_id
               ORDER BY rp.updated_at DESC"""
        ).fetchall()
        result = [dict(r) for r in rows]
        profiled_ids = {r["person_id"] for r in result}
        people_rows = conn.execute("SELECT * FROM people ORDER BY last_mentioned DESC").fetchall()
        for p in people_rows:
            if p["id"] not in profiled_ids:
                d = dict(p)
                d["person_id"] = d["id"]
                result.append(d)
        return result


# ── Relationship Events ───────────────────────────────────────

def add_relationship_event(person_id: int, event_type: str, summary: str,
                           event_date: str = None, impact_on_user: str = None,
                           lessons: str = None, source: str = "imessage") -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO relationship_events
               (person_id, event_date, event_type, summary, impact_on_user, lessons, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (person_id, event_date, event_type, summary, impact_on_user, lessons, source),
        )
        return cur.lastrowid


def get_relationship_timeline(person_id: int, limit: int = 20) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM relationship_events
               WHERE person_id = ?
               ORDER BY COALESCE(event_date, created_at) DESC
               LIMIT ?""",
            (person_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Cross Insights ────────────────────────────────────────────

def add_cross_insight(insight_type: str, content: str,
                      people_involved: list = None, evidence: str = None,
                      actionable: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO cross_insights
               (insight_type, content, people_involved, evidence, actionable)
               VALUES (?, ?, ?, ?, ?)""",
            (insight_type, content, json.dumps(people_involved or []), evidence, actionable),
        )
        return cur.lastrowid


def get_active_insights() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cross_insights WHERE status = 'active' ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def increment_insight(insight_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE cross_insights SET occurrences = occurrences + 1, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
            (insight_id,),
        )


# ── Life Context ──────────────────────────────────────────────

def add_life_context(context_type: str, description: str,
                     period_start: str = None, period_end: str = None,
                     impact_on_mood: str = None,
                     impact_on_productivity: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO life_context
               (context_type, description, period_start, period_end,
                impact_on_mood, impact_on_productivity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (context_type, description, period_start, period_end,
             impact_on_mood, impact_on_productivity),
        )
        return cur.lastrowid


def get_active_life_context() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM life_context WHERE active = 1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def close_life_context(context_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE life_context SET active = 0, period_end = DATE('now') WHERE id = ?",
            (context_id,),
        )


# ── iMessage Analysis Cache ───────────────────────────────────

def get_analysis_cursor(handle: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_analyzed_rowid FROM imessage_analysis_cache WHERE handle = ?",
            (handle,),
        ).fetchone()
        return row["last_analyzed_rowid"] if row else 0


def update_analysis_cursor(handle: str, last_rowid: int, messages_count: int) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO imessage_analysis_cache (handle, last_analyzed_rowid, last_analyzed_at, total_messages_analyzed)
               VALUES (?, ?, CURRENT_TIMESTAMP, ?)
               ON CONFLICT(handle)
               DO UPDATE SET last_analyzed_rowid = excluded.last_analyzed_rowid,
                            last_analyzed_at = excluded.last_analyzed_at,
                            total_messages_analyzed = total_messages_analyzed + excluded.total_messages_analyzed""",
            (handle, last_rowid, messages_count),
        )


def get_total_messages_analyzed(handle: str) -> int:
    """Retourne le nombre total de messages analysés pour un handle iMessage."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT total_messages_analyzed FROM imessage_analysis_cache WHERE handle = ?",
            (handle,),
        ).fetchone()
        return row["total_messages_analyzed"] if row else 0


def sync_imessage_counts_to_people() -> int:
    """Synchronise les compteurs imessage_count dans people depuis la cache d'analyse.
    
    Returns le nombre de mises à jour effectuées.
    """
    with get_db() as conn:
        # Mise à jour via JOIN entre people (via relationship_profiles.handle) et imessage_analysis_cache
        result = conn.execute(
            """
            UPDATE people SET imessage_count = (
                SELECT iac.total_messages_analyzed
                FROM relationship_profiles rp
                JOIN imessage_analysis_cache iac ON LOWER(rp.handle) = LOWER(iac.handle)
                WHERE rp.person_id = people.id
            )
            WHERE id IN (
                SELECT rp.person_id FROM relationship_profiles rp
                JOIN imessage_analysis_cache iac ON LOWER(rp.handle) = LOWER(iac.handle)
            )
            """
        )
        return result.rowcount


def _normalize_handle_for_match(handle: str) -> str:
    h = (handle or "").strip().lower()
    if not h:
        return ""
    if "@" in h:
        return h
    digits = re.sub(r"[^\d]", "", h)
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    elif digits.startswith("0033") and len(digits) >= 13:
        digits = "0" + digits[4:]
    if len(digits) > 10:
        digits = digits[-10:]
    return digits or h


def _merge_people_ids(conn: sqlite3.Connection, keep_id: int, drop_id: int) -> None:
    """Fusionne drop_id vers keep_id dans les tables relationnelles."""
    if keep_id == drop_id:
        return
    conn.execute("UPDATE people_events SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
    conn.execute("UPDATE relationship_events SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
    conn.execute("UPDATE relationship_profiles SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
    conn.execute("DELETE FROM people WHERE id = ?", (drop_id,))


def force_upsert_people_from_mac_sync(records: list[dict[str, Any]]) -> dict[str, int]:
    """UPSERT massif depuis sync macOS (contacts + iMessage), avec correction dates.

    Chaque record peut contenir:
      - handle
      - name
      - msg_count
      - first_message_at / last_message_at (ISO)
      - last_rowid
    """
    stats = {
        "input_records": len(records),
        "created": 0,
        "updated": 0,
        "dates_corrected": 0,
        "profiles_upserted": 0,
        "cache_upserted": 0,
        "merged_duplicates": 0,
    }
    if not records:
        return stats

    with get_db() as conn:
        # Index existants
        people_rows = conn.execute("SELECT id, name, last_mentioned, COALESCE(imessage_count, 0) AS imessage_count FROM people").fetchall()
        by_name: dict[str, dict] = {str(r["name"]).strip().lower(): dict(r) for r in people_rows if r["name"]}

        profile_rows = conn.execute("SELECT id, person_id, handle FROM relationship_profiles WHERE handle IS NOT NULL").fetchall()
        by_handle_norm: dict[str, int] = {}
        for r in profile_rows:
            hn = _normalize_handle_for_match(str(r["handle"] or ""))
            if hn:
                by_handle_norm[hn] = int(r["person_id"])

        for rec in records:
            raw_handle = str(rec.get("handle") or "").strip()
            handle_norm = _normalize_handle_for_match(raw_handle)
            name = str(rec.get("name") or "").strip() or raw_handle or "Contact inconnu"
            name_key = name.lower()
            msg_count = int(rec.get("msg_count") or 0)
            last_rowid = int(rec.get("last_rowid") or 0)
            last_message_at = str(rec.get("last_message_at") or "").strip() or None

            person_id = None
            if handle_norm and handle_norm in by_handle_norm:
                person_id = by_handle_norm[handle_norm]
            elif name_key in by_name:
                person_id = int(by_name[name_key]["id"])

            if person_id is None:
                cur = conn.execute(
                    "INSERT INTO people (name, relationship, last_mentioned, imessage_count) VALUES (?, ?, ?, ?)",
                    (name, "connaissance", last_message_at, msg_count),
                )
                person_id = int(cur.lastrowid)
                stats["created"] += 1
                by_name[name_key] = {
                    "id": person_id,
                    "name": name,
                    "last_mentioned": last_message_at,
                    "imessage_count": msg_count,
                }
            else:
                row = conn.execute(
                    "SELECT name, last_mentioned, COALESCE(imessage_count,0) AS imessage_count FROM people WHERE id = ?",
                    (person_id,),
                ).fetchone()
                old_last = (row["last_mentioned"] or "") if row else ""
                old_count = int((row["imessage_count"] or 0) if row else 0)
                new_last = old_last
                date_changed = False
                if last_message_at and (not old_last or last_message_at > old_last):
                    new_last = last_message_at
                    date_changed = True
                    stats["dates_corrected"] += 1
                new_count = max(old_count, msg_count)
                conn.execute(
                    "UPDATE people SET last_mentioned = ?, imessage_count = ? WHERE id = ?",
                    (new_last, new_count, person_id),
                )
                stats["updated"] += 1
                if date_changed:
                    by_name.setdefault(name_key, {"id": person_id, "name": name})["last_mentioned"] = new_last
                by_name.setdefault(name_key, {"id": person_id, "name": name})["imessage_count"] = new_count

            # UPsert profile par person_id
            rp = conn.execute("SELECT id FROM relationship_profiles WHERE person_id = ?", (person_id,)).fetchone()
            if rp:
                conn.execute(
                    "UPDATE relationship_profiles SET handle = ?, last_analyzed = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE person_id = ?",
                    (raw_handle or None, person_id),
                )
            else:
                conn.execute(
                    "INSERT INTO relationship_profiles (person_id, handle, last_analyzed) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (person_id, raw_handle or None),
                )
            stats["profiles_upserted"] += 1

            # Détection/fusion des doublons: même handle -> même person_id
            if handle_norm:
                previous_person_id = by_handle_norm.get(handle_norm)
                if previous_person_id and previous_person_id != person_id:
                    _merge_people_ids(conn, keep_id=previous_person_id, drop_id=person_id)
                    person_id = previous_person_id
                    stats["merged_duplicates"] += 1
                by_handle_norm[handle_norm] = person_id

            # imessage_analysis_cache: écraser avec l'état réel (pas + incrémental)
            if raw_handle:
                conn.execute(
                    """
                    INSERT INTO imessage_analysis_cache (handle, last_analyzed_rowid, last_analyzed_at, total_messages_analyzed)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(handle)
                    DO UPDATE SET
                        last_analyzed_rowid = CASE
                            WHEN excluded.last_analyzed_rowid > imessage_analysis_cache.last_analyzed_rowid
                                THEN excluded.last_analyzed_rowid
                            ELSE imessage_analysis_cache.last_analyzed_rowid
                        END,
                        last_analyzed_at = excluded.last_analyzed_at,
                        total_messages_analyzed = CASE
                            WHEN excluded.total_messages_analyzed > imessage_analysis_cache.total_messages_analyzed
                                THEN excluded.total_messages_analyzed
                            ELSE imessage_analysis_cache.total_messages_analyzed
                        END
                    """,
                    (raw_handle, last_rowid, msg_count),
                )
                stats["cache_upserted"] += 1

    return stats


# ── Contexte dense pour Sonnet ────────────────────────────────

def build_full_context() -> dict:
    """Construit le contexte complet structuré pour Sonnet.

    Retourne un dict avec TOUTES les données pertinentes de la mémoire.
    Sonnet ne voit jamais de messages bruts — que des données denses.
    """
    from database.location_helpers import (
        get_active_location_patterns,
        get_current_location,
        get_current_visit,
        get_today_visits,
    )

    return {
        "user_facts": get_all_facts_summary(),
        "life_profile": get_life_profile(),
        "active_patterns": get_active_patterns(),
        "active_life_context": get_active_life_context(),
        "recent_moods": get_recent_moods(14),
        "people_profiles": get_all_relationship_profiles(),
        "cross_insights": get_active_insights(),
        "recent_episodes": get_recent_episodes(limit=10),
        "current_location": get_current_location(),
        "current_visit": get_current_visit(),
        "today_visits": get_today_visits(),
        "location_patterns": get_active_location_patterns(),
    }


def get_last_conversation_summary() -> str | None:
    """Résumé textuel de la conversation terminée la plus récente (si présent)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT summary FROM conversations
               WHERE summary IS NOT NULL AND TRIM(summary) != ''
                 AND ended_at IS NOT NULL
               ORDER BY datetime(ended_at) DESC LIMIT 1"""
        ).fetchone()
    if not row or not row["summary"]:
        return None
    s = str(row["summary"]).strip()
    return s or None


def get_pattern(pattern_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        return dict(row) if row else None


def count_memory_stats() -> dict:
    """Compteurs pour tableaux de bord /api/status."""
    with get_db() as conn:
        def _one(query: str, params: tuple = ()) -> int:
            return int(conn.execute(query, params).fetchone()[0])

        return {
            "user_facts": _one("SELECT COUNT(*) FROM user_facts WHERE is_current = 1"),
            "relationship_profiles": _one("SELECT COUNT(*) FROM relationship_profiles"),
            "patterns_active": _one("SELECT COUNT(*) FROM patterns WHERE status = 'active'"),
            "episodes": _one("SELECT COUNT(*) FROM episodes"),
            "people": _one("SELECT COUNT(*) FROM people"),
            "cross_insights": _one("SELECT COUNT(*) FROM cross_insights WHERE status = 'active'"),
        }


def get_usage_stats() -> dict:
    with get_db() as conn:
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            """SELECT COUNT(*) as msg_count,
                      COALESCE(SUM(tokens_in), 0) as total_in,
                      COALESCE(SUM(tokens_out), 0) as total_out,
                      COALESCE(SUM(cost), 0) as total_cost
               FROM messages WHERE DATE(created_at) = ?""",
            (today,)
        ).fetchone()
        return dict(row)


def set_daily_ritual(date: str, field: str, value) -> None:
    """UPSERT d'un champ du rituel quotidien (roast/debrief/quote/score…)."""
    allowed = {"roast", "debrief", "quote", "productivity_score", "score_detail", "weekly_debrief"}
    if field not in allowed:
        raise ValueError(f"champ rituel invalide : {field}")
    with get_db() as conn:
        conn.execute(
            f"""INSERT INTO daily_rituals (date, {field}) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    {field} = excluded.{field},
                    updated_at = CURRENT_TIMESTAMP""",  # noqa: S608 — champ whitelisté
            (date, value),
        )


def get_daily_ritual(date: str) -> dict | None:
    """Retourne la ligne de rituels du jour demandé, ou None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_rituals WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None


def get_todays_birthdays(today_mm_dd: str | None = None) -> list[dict]:
    """Contacts dont l'anniversaire tombe aujourd'hui.

    ``people.birthday`` accepte 'YYYY-MM-DD' (âge calculable) ou 'MM-DD'.
    """
    mm_dd = today_mm_dd or datetime.now().strftime("%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, name, relationship, birthday FROM people
               WHERE birthday IS NOT NULL AND birthday != ''
                 AND (
                     substr(birthday, 6, 5) = ?  -- format YYYY-MM-DD
                     OR birthday = ?             -- format MM-DD
                 )""",
            (mm_dd, mm_dd),
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_mood_signal(date: str, signal: dict) -> None:
    """UPSERT du signal comportemental du jour."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO mood_signals
                   (date, msg_count, msg_avg_14d, deviation_pct, voice_count,
                    screen_minutes, late_night_points, flags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   msg_count = excluded.msg_count,
                   msg_avg_14d = excluded.msg_avg_14d,
                   deviation_pct = excluded.deviation_pct,
                   voice_count = excluded.voice_count,
                   screen_minutes = excluded.screen_minutes,
                   late_night_points = excluded.late_night_points,
                   flags = excluded.flags""",
            (
                date,
                signal.get("msg_count", 0),
                signal.get("msg_avg_14d", 0.0),
                signal.get("deviation_pct"),
                signal.get("voice_count", 0),
                signal.get("screen_minutes", 0.0),
                signal.get("late_night_points", 0),
                signal.get("flags"),
            ),
        )


def get_mood_signals(days: int = 14) -> list[dict]:
    """Signaux comportementaux des `days` derniers jours (récent en premier)."""
    days = max(1, min(days, 90))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM mood_signals ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


def open_presence_session(arrived_at: str) -> int:
    """Ouvre une session de présence. Retourne son id."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO presence_sessions (arrived_at) VALUES (?)", (arrived_at,)
        )
        return cur.lastrowid


def close_presence_session(session_id: int, left_at: str) -> None:
    """Ferme une session de présence et calcule sa durée en minutes."""
    with get_db() as conn:
        conn.execute(
            """UPDATE presence_sessions
               SET left_at = ?,
                   duration_min = ROUND((julianday(?) - julianday(arrived_at)) * 1440, 1)
               WHERE id = ? AND left_at IS NULL""",
            (left_at, left_at, session_id),
        )


def get_cost_summary() -> dict:
    """Dépenses LLM : aujourd'hui, 7 derniers jours, mois en cours, par modèle.

    Sert /api/costs et l'alerte budget. Les montants viennent de
    messages.cost (calculé à chaque appel par llm.estimate_cost).
    """
    from datetime import timedelta

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    week_start = (now.date() - timedelta(days=6)).isoformat()
    month_start = now.strftime("%Y-%m-01")
    with get_db() as conn:
        def _agg(where: str, params: tuple) -> dict:
            row = conn.execute(
                f"""SELECT COUNT(*) AS msg_count,
                           COALESCE(SUM(cost), 0) AS cost,
                           COALESCE(SUM(tokens_in), 0) AS tokens_in,
                           COALESCE(SUM(tokens_out), 0) AS tokens_out
                    FROM messages WHERE {where}""",
                params,
            ).fetchone()
            return dict(row)

        by_model = [dict(r) for r in conn.execute(
            """SELECT COALESCE(model, 'inconnu') AS model,
                      COUNT(*) AS msg_count,
                      COALESCE(SUM(cost), 0) AS cost
               FROM messages
               WHERE DATE(created_at) >= ? AND model IS NOT NULL
               GROUP BY COALESCE(model, 'inconnu')
               ORDER BY cost DESC""",
            (month_start,),
        )]
        return {
            "today": _agg("DATE(created_at) = ?", (today,)),
            "last_7_days": _agg("DATE(created_at) >= ?", (week_start,)),
            "month": _agg("DATE(created_at) >= ?", (month_start,)),
            "by_model_month": by_model,
            "budget_monthly": config.LLM_BUDGET_MONTHLY,
            "budget_alert_pct": config.LLM_BUDGET_ALERT_PCT,
        }


def get_daily_activity_stats(days: int = 7) -> list[dict]:
    """Activité agrégée par jour sur les `days` derniers jours (plus ancien en premier).

    Chaque entrée : {date, msg_count, voice_count, tokens_in, tokens_out, cost}.
    Les jours sans activité sont présents avec des compteurs à zéro, pour que
    les séries temporelles côté UI soient continues.
    """
    from datetime import timedelta

    days = max(1, min(days, 90))
    today = datetime.now().date()
    start = (today - timedelta(days=days - 1)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DATE(m.created_at) AS date,
                      COUNT(*) AS msg_count,
                      COALESCE(SUM(CASE WHEN c.agent = 'voice' THEN 1 ELSE 0 END), 0) AS voice_count,
                      COALESCE(SUM(m.tokens_in), 0) AS tokens_in,
                      COALESCE(SUM(m.tokens_out), 0) AS tokens_out,
                      COALESCE(SUM(m.cost), 0) AS cost
               FROM messages m
               LEFT JOIN conversations c ON c.id = m.conversation_id
               WHERE DATE(m.created_at) >= ?
               GROUP BY DATE(m.created_at)""",
            (start,),
        ).fetchall()
    by_date = {r["date"]: dict(r) for r in rows}
    out: list[dict] = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        out.append(by_date.get(d, {
            "date": d, "msg_count": 0, "voice_count": 0,
            "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
        }))
    return out


# ═══════════════════════════════════════════════════════════
# DAEMON JARVIS — helpers screen / app_usage / devices / work_sessions
# ═══════════════════════════════════════════════════════════

import secrets


def save_screen_activity(
    device: str,
    app: str | None,
    activity: str | None,
    mood: str | None = None,
    notable: str | None = None,
    screenshot_hash: str | None = None,
    change_pct: float | None = None,
) -> int:
    """Insère une ligne d'activité écran. Retourne l'id."""
    if mood and mood not in ("focused", "idle", "distracted", "stuck", "browsing", "unknown"):
        mood = "unknown"
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO screen_activity
               (device, app, activity, mood, notable, screenshot_hash, change_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device, app, activity, mood, notable, screenshot_hash, change_pct),
        )
        conn.execute(
            "UPDATE devices SET last_screen_at = CURRENT_TIMESTAMP WHERE device_id = ?",
            (device,),
        )
        return cur.lastrowid


def get_screen_activity(hours: int = 24, device: str | None = None) -> list[dict]:
    """Retourne les lignes d'activité écran sur N heures."""
    with get_db() as conn:
        if device:
            rows = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE created_at >= datetime('now', ?)
                     AND device = ?
                   ORDER BY created_at DESC""",
                (f"-{int(hours)} hours", device),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE created_at >= datetime('now', ?)
                   ORDER BY created_at DESC""",
                (f"-{int(hours)} hours",),
            ).fetchall()
        return [dict(r) for r in rows]


def get_current_screen_context(device: str | None = None) -> dict | None:
    """Dernier `screen_activity` (au plus 5 minutes pour rester pertinent)."""
    with get_db() as conn:
        if device:
            row = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE device = ?
                     AND created_at >= datetime('now', '-5 minutes')
                   ORDER BY created_at DESC LIMIT 1""",
                (device,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE created_at >= datetime('now', '-5 minutes')
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None


def upsert_app_usage(device: str, app: str, seconds: int) -> None:
    """Incrémente le temps cumulé pour (device, app, date_du_jour).

    Si pas d'entrée du jour : crée avec session_count=1.
    Si déjà présent : ajoute `seconds` et incrémente `session_count` de 1
    (chaque appel = nouvelle session, le caller décide quand l'app change).
    """
    if not app or seconds <= 0:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO app_usage (device, app, date, duration_seconds, session_count)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(device, app, date) DO UPDATE SET
                   duration_seconds = duration_seconds + excluded.duration_seconds,
                   session_count = session_count + 1""",
            (device, app, today, int(seconds)),
        )


def get_app_usage(date: str | None = None, device: str | None = None) -> list[dict]:
    """Liste des temps par app pour un jour (défaut : aujourd'hui)."""
    target = date or datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        if device:
            rows = conn.execute(
                """SELECT * FROM app_usage
                   WHERE date = ? AND device = ?
                   ORDER BY duration_seconds DESC""",
                (target, device),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM app_usage
                   WHERE date = ?
                   ORDER BY duration_seconds DESC""",
                (target,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_app_usage_range(days: int = 7, device: str | None = None) -> list[dict]:
    """Liste des temps par app sur les N derniers jours, agrégés par (app, date)."""
    with get_db() as conn:
        if device:
            rows = conn.execute(
                """SELECT * FROM app_usage
                   WHERE date >= date('now', ?) AND device = ?
                   ORDER BY date DESC, duration_seconds DESC""",
                (f"-{int(days)} days", device),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM app_usage
                   WHERE date >= date('now', ?)
                   ORDER BY date DESC, duration_seconds DESC""",
                (f"-{int(days)} days",),
            ).fetchall()
        return [dict(r) for r in rows]


def register_device(
    device_id: str,
    device_name: str,
    device_type: str = "desktop",
    ip_tailscale: str | None = None,
) -> str:
    """Crée ou met à jour la machine. Génère un auth_token si nouveau. Retourne le token."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT auth_token FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row and row["auth_token"]:
            conn.execute(
                """UPDATE devices SET
                       device_name = ?, device_type = ?, ip_tailscale = COALESCE(?, ip_tailscale),
                       is_online = 1, last_heartbeat = CURRENT_TIMESTAMP
                   WHERE device_id = ?""",
                (device_name, device_type, ip_tailscale, device_id),
            )
            return row["auth_token"]
        token = secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO devices
               (device_id, device_name, device_type, is_online, last_heartbeat,
                ip_tailscale, auth_token)
               VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, ?, ?)""",
            (device_id, device_name, device_type, ip_tailscale, token),
        )
        return token


def update_device_heartbeat(device_id: str) -> None:
    """Met à jour `last_heartbeat` et marque la machine en ligne."""
    with get_db() as conn:
        conn.execute(
            """UPDATE devices SET
                   last_heartbeat = CURRENT_TIMESTAMP,
                   is_online = 1
               WHERE device_id = ?""",
            (device_id,),
        )


def set_active_device(device_id: str) -> None:
    """Met `is_active=0` sur toutes les machines, `is_active=1` sur celle-ci."""
    with get_db() as conn:
        conn.execute("UPDATE devices SET is_active = 0")
        conn.execute("UPDATE devices SET is_active = 1 WHERE device_id = ?", (device_id,))


def get_active_device() -> dict | None:
    """Machine actuellement marquée active (l'écran analysée par défaut)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_all_devices() -> list[dict]:
    """Liste de toutes les machines enregistrées."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM devices ORDER BY is_active DESC, last_heartbeat DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_device_offline(device_id: str) -> None:
    """Marque une machine comme déconnectée."""
    with get_db() as conn:
        conn.execute(
            "UPDATE devices SET is_online = 0 WHERE device_id = ?", (device_id,)
        )


def start_work_session(device: str, app: str) -> int:
    """Crée une session de travail. Retourne l'id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO work_sessions (device, app, started_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (device, app),
        )
        return cur.lastrowid


def end_work_session(session_id: int, description: str | None = None) -> None:
    """Termine une session, calcule la durée, persiste la description."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT started_at FROM work_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return
        try:
            started = datetime.fromisoformat(row["started_at"].replace("Z", ""))
            duration = (datetime.now() - started).total_seconds() / 60.0
        except Exception:
            duration = None
        conn.execute(
            """UPDATE work_sessions SET
                   ended_at = CURRENT_TIMESTAMP,
                   duration_min = ?,
                   description = COALESCE(?, description)
               WHERE id = ?""",
            (duration, description, session_id),
        )


def get_work_sessions(days: int = 7) -> list[dict]:
    """Liste des sessions de travail récentes."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM work_sessions
               WHERE started_at >= datetime('now', ?)
               ORDER BY started_at DESC""",
            (f"-{int(days)} days",),
        ).fetchall()
        return [dict(r) for r in rows]


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


def _save_voice_debug_trace(trace: dict[str, Any]) -> None:
    """Sauvegarde une trace de debug vocal en DB (fire-and-forget, silencieux).

    Args:
        trace: dict contenant les champs du debug trace (input_text, system_prompt,
               messages_sent, raw_response, response_clean, emotion, action_detected,
               model, tokens_in, tokens_out, cost, latency_*).
    """
    import json as _json

    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO voice_debug_log
                   (input_text, system_prompt, messages_json, raw_response, response_clean,
                    emotion, action_json, model, tokens_in, tokens_out, cost,
                    latency_stt_ms, latency_llm1_ms, latency_llm2_ms, latency_tts_ms,
                    latency_total_ms, stt_engine, tts_engine, audio_duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(trace.get("input_text", ""))[:3000] if trace.get("input_text") else "",
                    str(trace.get("system_prompt", ""))[:50000] if trace.get("system_prompt") else "",
                    _json.dumps(trace.get("messages_sent", []), ensure_ascii=False) if trace.get("messages_sent") else "",
                    str(trace.get("raw_response", ""))[:10000] if trace.get("raw_response") else "",
                    str(trace.get("response_clean", ""))[:5000] if trace.get("response_clean") else "",
                    str(trace.get("emotion", "")),
                    _json.dumps(trace.get("action_detected"), ensure_ascii=False) if trace.get("action_detected") else None,
                    str(trace.get("model", "")),
                    int(trace.get("tokens_in", 0)),
                    int(trace.get("tokens_out", 0)),
                    float(trace.get("cost", 0.0)),
                    int(trace.get("latency_stt_ms", 0)),
                    int(trace.get("latency_llm_pass1_ms", 0)),
                    int(trace.get("latency_llm_pass2_ms", 0)),
                    int(trace.get("latency_tts_ms", 0)),
                    int(trace.get("latency_total_ms", 0)),
                    str(trace.get("stt_engine", "")),
                    str(trace.get("tts_engine", "")),
                    int(trace.get("audio_duration_ms", 0)),
                ),
            )
    except Exception:
        pass  # Fire-and-forget — ne jamais crasher le pipeline vocal


def get_voice_debug_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Récupère les dernières traces de debug vocal.

    Args:
        limit: Nombre maximum de traces à retourner (défaut 50).

    Returns:
        Liste de dicts, ordre décroissant par id (plus récent d'abord).
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM voice_debug_log ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Init au premier import ──────────────────────────────────
if __name__ == "__main__":
    init_db()


# ── Message Intelligence helpers ─────────────────────────────


def get_messages_since(
    since_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    """Récupère les messages (table messages) postérieurs à ``since_id``.

    Args:
        since_id: ID du dernier message déjà traité.
        limit: Nombre max de messages à retourner.

    Returns:
        Liste de dicts {id, content, role, created_at}. Vide si aucun nouveau message.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, content, role, created_at
               FROM messages
               WHERE id > ?
               ORDER BY id ASC
               LIMIT ?""",
            (since_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def save_message_insight(
    since_id: int,
    raw_response: str,
    message_count: int,
) -> int:
    """Persiste un insight généré à partir des messages.

    Args:
        since_id: ID du dernier message couvert par cet insight.
        raw_response: Contenu dé-anonymisé de la réponse DeepSeek (JSON stringifié).
        message_count: Nombre de messages analysés.

    Returns:
        ID de la ligne insérée.
    """
    import json as _json

    # Valide que le JSON est bien formé avant l'insertion.
    if isinstance(raw_response, dict):
        raw_response = _json.dumps(raw_response, ensure_ascii=False)
    else:
        try:
            _json.loads(raw_response)
        except (_json.JSONDecodeError, ValueError):
            # Enveloppe le texte brut dans un JSON pour éviter les corruptions.
            raw_response = _json.dumps(
                {"raw": raw_response}, ensure_ascii=False
            )

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO message_insights
               (since_message_id, message_count, result_json)
               VALUES (?, ?, ?)""",
            (since_id, message_count, raw_response),
        )
        return cur.lastrowid
