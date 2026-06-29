-- JARVIS — Schéma SQLite complet
-- Exécuter avec : sqlite3 data/jarvis.db < database/schema.sql

-- ═══ MÉMOIRE ═══

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
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    tags TEXT,
    embedding BLOB,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══ LIFE COACH ═══

CREATE TABLE IF NOT EXISTS life_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK(category IN ('values','goals','fears','patterns','strengths')),
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
    event_type TEXT CHECK(event_type IN ('conversation','insight','conflict','milestone','note')),
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
    pattern_type TEXT CHECK(pattern_type IN ('behavioral','emotional','relational','productivity','health')),
    description TEXT NOT NULL,
    occurrences INTEGER DEFAULT 1,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','resolved','monitoring'))
);

-- ═══ ÉCOLE ═══

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
    doc_type TEXT CHECK(doc_type IN ('cours','exercice','devoir','fiche','autre')),
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

-- ═══ PRODUCTIVITÉ ═══

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('high','medium','low')),
    status TEXT DEFAULT 'todo' CHECK(status IN ('todo','doing','done')),
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
    priority TEXT CHECK(priority IN ('high','medium','low')),
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE UNIQUE,
    morning_briefing TEXT,
    evening_summary TEXT,
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

-- ═══ RÉSUMÉS ═══

CREATE TABLE IF NOT EXISTS weekly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start DATE,
    summary TEXT,
    patterns_spotted TEXT,
    recommendations TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══ INDEX ═══

CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent);
CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);
CREATE INDEX IF NOT EXISTS idx_mood_created ON mood_log(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_flashcards_review ON school_flashcards(next_review);
CREATE INDEX IF NOT EXISTS idx_patterns_status ON patterns(status);

-- ═══ MÉMOIRE PROFONDE ═══

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

-- ═══ DAEMON JARVIS — ACTIVITÉ ÉCRAN ═══

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

-- ═══ DAEMON JARVIS — TEMPS PAR APPLICATION ═══

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

-- ═══ DAEMON JARVIS — MACHINES CONNECTÉES ═══

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

-- ═══ DAEMON JARVIS — SESSIONS DE TRAVAIL ═══

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
