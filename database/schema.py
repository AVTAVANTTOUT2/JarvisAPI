"""Schéma SQLite déclaratif de JARVIS."""

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

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    timestamp REAL NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    processed_by TEXT,
    processed_at REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_event_log_timestamp ON event_log(timestamp);

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
CREATE INDEX IF NOT EXISTS idx_notif_dedup ON notifications(source, title, email_id, created_at DESC);
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
    cloud_consent BOOLEAN NOT NULL DEFAULT 0,
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
    token_hash TEXT,
    revoked INTEGER DEFAULT 0,
    paired_at DATETIME,
    token_rotated_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_id ON devices(device_id);

CREATE TABLE IF NOT EXISTS device_pairing_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT UNIQUE NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    used_at DATETIME
);

CREATE TABLE IF NOT EXISTS device_pairing_attempts (
    client_key TEXT PRIMARY KEY,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    window_started_at DATETIME NOT NULL,
    blocked_until DATETIME
);

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

-- ═══════════════════════════════════════════════════════════
-- IMPORT iMessage — données brutes depuis chat.db
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS imessage_handles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apple_handle_id INTEGER UNIQUE NOT NULL,
    handle TEXT NOT NULL,
    country TEXT,
    service TEXT DEFAULT 'iMessage',
    uncanonicalized_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_handles_apple ON imessage_handles(apple_handle_id);
CREATE INDEX IF NOT EXISTS idx_imessage_handles_value ON imessage_handles(handle);

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
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_chats_apple ON imessage_chats(apple_chat_id);
CREATE INDEX IF NOT EXISTS idx_imessage_chats_identifier ON imessage_chats(chat_identifier);

CREATE TABLE IF NOT EXISTS imessage_chat_handles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES imessage_chats(id),
    handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
    UNIQUE(chat_id, handle_id)
);
CREATE INDEX IF NOT EXISTS idx_imessage_ch_handle ON imessage_chat_handles(handle_id);
CREATE INDEX IF NOT EXISTS idx_imessage_ch_chat ON imessage_chat_handles(chat_id);

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
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_rowid ON imessage_messages(apple_rowid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_guid ON imessage_messages(guid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_msg_hash ON imessage_messages(content_hash);
CREATE INDEX IF NOT EXISTS idx_imessage_msg_chat ON imessage_messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_imessage_msg_handle ON imessage_messages(handle_id);
CREATE INDEX IF NOT EXISTS idx_imessage_msg_date ON imessage_messages(date);
CREATE INDEX IF NOT EXISTS idx_imessage_msg_associated ON imessage_messages(associated_message_guid);

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
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_att_apple ON imessage_attachments(apple_attachment_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_att_guid ON imessage_attachments(guid);

CREATE TABLE IF NOT EXISTS imessage_message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
    attachment_id INTEGER NOT NULL REFERENCES imessage_attachments(id),
    UNIQUE(message_id, attachment_id)
);
CREATE INDEX IF NOT EXISTS idx_imessage_ma_msg ON imessage_message_attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_imessage_ma_att ON imessage_message_attachments(attachment_id);

CREATE TABLE IF NOT EXISTS imessage_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
    reactor_handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
    reaction_type INTEGER NOT NULL,
    apple_associated_guid TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(message_id, reactor_handle_id)
);
CREATE INDEX IF NOT EXISTS idx_imessage_reactions_msg ON imessage_reactions(message_id);

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
);

CREATE TABLE IF NOT EXISTS imessage_consumer_cursors (
    consumer TEXT PRIMARY KEY,
    last_apple_rowid INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════
-- DÉLÉGATION CURSOR CLI (jobs persistants)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cursor_delegation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    user_request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    repository TEXT,
    working_directory TEXT,
    worktree_path TEXT,
    branch_name TEXT,
    prompt_template TEXT,
    template_version TEXT,
    prompt_sent TEXT,
    raw_output TEXT,
    structured_result TEXT,
    acceptance_criteria TEXT,
    required_tests TEXT,
    risk_level TEXT DEFAULT 'medium',
    allow_commit INTEGER DEFAULT 1,
    allow_push INTEGER DEFAULT 1,
    allow_pr INTEGER DEFAULT 1,
    allow_merge INTEGER DEFAULT 0,
    commit_sha TEXT,
    pr_url TEXT,
    error_message TEXT,
    interaction_mode TEXT,
    routing_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    finished_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_cursor_jobs_status ON cursor_delegation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_cursor_jobs_created ON cursor_delegation_jobs(created_at);
"""
