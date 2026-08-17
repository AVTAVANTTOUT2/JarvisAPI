-- GENERATED FILE — DO NOT EDIT.
-- Source: database/schema.py + database/migrations.py + database/devagent.py.
-- Regenerate: python tools/audit_architecture_truth.py --schema-output database/schema.sql
-- This artifact is not executed by init_db(); it mirrors a fresh runtime schema.

CREATE TABLE agent_approval_outbox (
    approval_id TEXT PRIMARY KEY REFERENCES agent_approvals(approval_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    delivered_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(profile_id, decision_id),
    CHECK(decision IN ('approved', 'denied')),
    CHECK(status IN ('pending', 'delivering', 'delivered'))
);

CREATE TABLE agent_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    action TEXT NOT NULL,
    tool TEXT NOT NULL,
    summary TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    risks_json TEXT NOT NULL DEFAULT '[]',
    scope TEXT NOT NULL,
    expires_at TEXT,
    decision TEXT NOT NULL DEFAULT 'pending',
    decision_by TEXT,
    decision_at TEXT,
    decision_id TEXT,
    created_at TEXT NOT NULL,
    CHECK(decision IN ('pending', 'approved', 'denied', 'expired'))
);

CREATE TABLE agent_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    reference TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    visibility TEXT NOT NULL DEFAULT 'user',
    retention TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL
);

CREATE TABLE agent_capability_grants (
    grant_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    scope TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, capability, scope)
);

CREATE TABLE agent_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);

CREATE TABLE agent_event_inbox (
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    processing_started_at TEXT,
    processed_at TEXT,
    claim_token TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, event_id),
    FOREIGN KEY(run_id, event_id)
        REFERENCES agent_events(run_id, event_id) ON DELETE CASCADE
);

CREATE TABLE agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    event_type TEXT NOT NULL,
    external_event_id TEXT,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    level TEXT NOT NULL DEFAULT 'info',
    visibility TEXT NOT NULL DEFAULT 'user',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    UNIQUE(run_id, event_id),
    UNIQUE(run_id, sequence)
);

CREATE TABLE agent_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL
);

CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    task_id TEXT,
    conversation_id TEXT,
    origin TEXT NOT NULL,
    channel TEXT NOT NULL,
    device TEXT,
    locale TEXT NOT NULL DEFAULT 'fr-FR',
    timezone TEXT NOT NULL DEFAULT 'Europe/Paris',
    runtime_id TEXT NOT NULL,
    provider_session_id TEXT,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    context_json TEXT NOT NULL DEFAULT '{}',
    budget_json TEXT NOT NULL,
    workspace TEXT,
    idempotency_key TEXT,
    idempotency_digest TEXT,
    error_json TEXT,
    verification_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK(status IN (
        'created', 'classified', 'queued', 'provisioning', 'planning',
        'awaiting_approval', 'running', 'verifying', 'reviewing', 'paused',
        'blocked', 'cancelling', 'cancelled', 'failed', 'completed', 'expired',
        'provider_unavailable'
    )),
    CHECK(category IN (
        'direct_action', 'workflow', 'agentic_readonly', 'agentic_reversible',
        'agentic_external_effect', 'agentic_high_risk'
    ))
);

CREATE TABLE agent_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(run_id, sequence)
);

CREATE TABLE agentic_workflows (
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

CREATE TABLE app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE app_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL DEFAULT 'mac_mini',
    app TEXT NOT NULL,
    date DATE NOT NULL,
    duration_seconds INTEGER DEFAULT 0,
    session_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device, app, date)
);

CREATE TABLE apple_shortcut_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
    alias TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    allow_input INTEGER NOT NULL DEFAULT 0 CHECK(allow_input IN (0, 1)),
    requires_confirmation INTEGER NOT NULL DEFAULT 1
        CHECK(requires_confirmation IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    risk TEXT NOT NULL DEFAULT 'medium'
        CHECK(risk IN ('low', 'medium', 'high')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_run_at DATETIME,
    run_count INTEGER NOT NULL DEFAULT 0 CHECK(run_count >= 0),
    UNIQUE(name COLLATE NOCASE)
);

CREATE TABLE apple_shortcut_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registry_id INTEGER REFERENCES apple_shortcut_registry(id) ON DELETE SET NULL,
    shortcut_name TEXT NOT NULL,
    ok INTEGER NOT NULL CHECK(ok IN (0, 1)),
    input_preview TEXT,
    output_preview TEXT,
    error TEXT,
    plan_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE auth_rate_limits (
            client_key TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            window_started_at TEXT NOT NULL,
            blocked_until TEXT,
            updated_at TEXT NOT NULL
        );

CREATE TABLE calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE,
    calendar_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT,
    location TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    is_all_day INTEGER NOT NULL DEFAULT 0 CHECK(is_all_day IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE commitments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            made_to TEXT,
            due_hint TEXT,
            source TEXT DEFAULT 'conversation',
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'kept', 'dropped')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        );

CREATE TABLE connector_bindings (
    source TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    connector_kind TEXT NOT NULL,
    account_ref TEXT NOT NULL DEFAULT 'local',
    device_id_hash TEXT NOT NULL DEFAULT '',
    external_account_hash TEXT NOT NULL DEFAULT '',
    permission_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK(permission_state IN ('unknown', 'granted', 'denied')),
    consent_source TEXT NOT NULL DEFAULT 'explicit',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    sync_interval_seconds INTEGER NOT NULL DEFAULT 300 CHECK(sync_interval_seconds >= 15),
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE contact_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_type TEXT NOT NULL CHECK(identity_type IN ('email', 'phone', 'imessage', 'handle')),
    normalized_value TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(identity_type, normalized_value)
);

CREATE TABLE control_task_activity (
    activity_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    run_id TEXT,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    agent_role TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    artifact_reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'detail',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, sequence)
);

CREATE TABLE control_task_candidates (
    candidate_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    suggested_title TEXT NOT NULL,
    suggested_description TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    suggested_due_at TEXT,
    decision TEXT NOT NULL DEFAULT 'pending',
    decision_at TEXT,
    created_task_id TEXT,
    duplicate_of TEXT,
    dedupe_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE control_task_comments (
    comment_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'user',
    body TEXT NOT NULL,
    run_id TEXT,
    plan_version INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE control_task_plans (
    plan_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    objective TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    context_understood TEXT NOT NULL DEFAULT '',
    steps_json TEXT NOT NULL DEFAULT '[]',
    deliverables_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    permissions_json TEXT NOT NULL DEFAULT '[]',
    execution_permissions_json TEXT NOT NULL DEFAULT '[]',
    risks_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    success_criteria_json TEXT NOT NULL DEFAULT '[]',
    known_limits_json TEXT NOT NULL DEFAULT '[]',
    estimated_duration_s INTEGER,
    estimated_cost REAL,
    created_by TEXT NOT NULL DEFAULT 'jarvis.planner',
    created_at TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending',
    decision_at TEXT,
    decision_by TEXT,
    decision_comment TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE control_task_reports (
    report_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(task_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    result_status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    markdown TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE control_tasks (
    task_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_channel TEXT NOT NULL DEFAULT 'api',
    source_reference TEXT NOT NULL DEFAULT '',
    source_excerpt TEXT NOT NULL DEFAULT '',
    source_confidence REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    project_id TEXT,
    conversation_id TEXT,
    due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    plan_id TEXT,
    plan_version INTEGER,
    approved_plan_version INTEGER,
    approved_plan_digest TEXT,
    agentic_run_id TEXT,
    current_phase TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    attention_required INTEGER NOT NULL DEFAULT 0,
    result_status TEXT,
    final_report_id TEXT,
    legacy_task_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE conversation_documents (
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

CREATE TABLE conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            turn_order INTEGER NOT NULL,
            speaker_label TEXT NOT NULL,
            person_id INTEGER REFERENCES people(id),
            text TEXT NOT NULL,
            start_ms INTEGER,
            end_ms INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id TEXT NOT NULL UNIQUE DEFAULT (
        lower(hex(randomblob(4))) || '-' ||
        lower(hex(randomblob(2))) || '-4' ||
        substr(lower(hex(randomblob(2))), 2) || '-' ||
        substr('89ab', (random() & 3) + 1, 1) ||
        substr(lower(hex(randomblob(2))), 2) || '-' ||
        lower(hex(randomblob(6)))
    ),
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    agent TEXT,
    summary TEXT,
    title TEXT,
    title_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(title_status IN ('pending', 'ready', 'fallback', 'manual')),
    title_source TEXT,
    title_updated_at DATETIME,
    pinned BOOLEAN DEFAULT 0,
    archived BOOLEAN DEFAULT 0,
    tags TEXT,
    last_message_at DATETIME,
    message_count INTEGER DEFAULT 0,
    mood_start INTEGER,
    mood_end INTEGER
);

CREATE TABLE cross_insights (
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

CREATE TABLE cursor_delegation_jobs (
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

CREATE TABLE daily_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE UNIQUE,
    morning_briefing TEXT,
    evening_summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE daily_rituals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            roast TEXT,
            debrief TEXT,
            quote TEXT,
            productivity_score INTEGER,
            score_detail TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        , weekly_debrief TEXT);

CREATE TABLE day_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            exceptional_score INTEGER,
            luck_score INTEGER,
            factors_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE dev_deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    commit_sha TEXT,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
    staging_path TEXT,
    log TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dev_interview_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    context_json TEXT NOT NULL DEFAULT '{}',
    questions_asked INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dev_loop_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    iteration INTEGER,
    phase TEXT,
    content TEXT,
    success BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dev_loop_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    iteration INTEGER DEFAULT 0,
    phase TEXT,
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dev_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    project_type TEXT,
    status TEXT DEFAULT 'interviewing',
    isolation_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dev_spec (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES dev_projects(id),
    spec_json TEXT NOT NULL,
    locked_at TIMESTAMP,
    version INTEGER DEFAULT 1
);

CREATE TABLE device_pairing_attempts (
    client_key TEXT PRIMARY KEY,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    window_started_at DATETIME NOT NULL,
    blocked_until DATETIME
);

CREATE TABLE device_pairing_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT UNIQUE NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    used_at DATETIME
);

CREATE TABLE devices (
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

CREATE TABLE duplicate_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_a TEXT NOT NULL, start_a INTEGER NOT NULL, end_a INTEGER NOT NULL,
            file_b TEXT NOT NULL, start_b INTEGER NOT NULL, end_b INTEGER NOT NULL,
            lines_count INTEGER NOT NULL,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'refactored', 'ignored')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_a, start_a, file_b, start_b)
        );

CREATE TABLE email_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_id TEXT UNIQUE,
    sender TEXT,
    subject TEXT,
    summary TEXT,
    action_needed BOOLEAN DEFAULT 0,
    priority TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    body TEXT DEFAULT '',
    received_at TEXT DEFAULT '',
    received_at_utc TEXT,
    source_updated_at_utc TEXT,
    account_id TEXT,
    mailbox_id TEXT,
    category TEXT DEFAULT 'info',
    is_read INTEGER DEFAULT 0,
    content_complete INTEGER NOT NULL DEFAULT 0 CHECK(content_complete IN (0, 1)),
    ingestion_completeness TEXT NOT NULL DEFAULT 'metadata'
        CHECK(ingestion_completeness IN ('metadata', 'partial', 'complete')),
    sender_identity_id INTEGER REFERENCES contact_identities(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT ''
);

CREATE TABLE episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER REFERENCES recordings(id) ON DELETE SET NULL,
    agent TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    tags TEXT,                   -- JSON array
    embedding BLOB,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    timestamp REAL NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL
);

CREATE TABLE fitness_program_sessions (
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

CREATE TABLE fitness_programs (
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

CREATE TABLE fitness_prompt_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('workout', 'meal')),
            reference TEXT NOT NULL,
            prompted_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(date, kind, reference, prompted_at)
        );

CREATE TABLE fitness_session_progress (
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

CREATE TABLE fitness_weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            weight_kg REAL NOT NULL CHECK(weight_kg BETWEEN 20 AND 500),
            notes TEXT,
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

CREATE TABLE food_menu_cache (
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

CREATE TABLE food_orders (
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
    -- Avancement de la livraison, distinct de `status` qui décrit l'issue de
    -- la tentative de commande. Une commande peut être 'placed' côté JARVIS
    -- et encore 'preparing' côté restaurant.
    delivery_status TEXT CHECK(
        delivery_status IS NULL OR delivery_status IN (
            'placed', 'preparing', 'picked_up', 'on_the_way', 'delivered', 'cancelled'
        )
    ),
    eta_minutes INTEGER CHECK(eta_minutes IS NULL OR eta_minutes >= 0),
    delivered_at DATETIME,
    tracking_url TEXT,
    rating INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5),
    suggestion_id INTEGER REFERENCES food_suggestions(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE food_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5
        CHECK(confidence >= 0.0 AND confidence <= 1.0),
    sample_size INTEGER NOT NULL DEFAULT 0 CHECK(sample_size >= 0),
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE food_suggestions (
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

CREATE TABLE imessage_analysis_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    last_analyzed_rowid INTEGER DEFAULT 0,
    last_analyzed_at DATETIME,
    total_messages_analyzed INTEGER DEFAULT 0
);

CREATE TABLE imessage_attachments (
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

CREATE TABLE imessage_chat_handles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES imessage_chats(id),
    handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
    UNIQUE(chat_id, handle_id)
);

CREATE TABLE imessage_chats (
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

CREATE TABLE imessage_consumer_cursors (
    consumer TEXT PRIMARY KEY,
    last_apple_rowid INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE imessage_handles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apple_handle_id INTEGER UNIQUE NOT NULL,
    handle TEXT NOT NULL,
    country TEXT,
    service TEXT DEFAULT 'iMessage',
    uncanonicalized_id TEXT,
    display_name TEXT NOT NULL DEFAULT '',
    contact_identity_id INTEGER REFERENCES contact_identities(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE imessage_message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
    attachment_id INTEGER NOT NULL REFERENCES imessage_attachments(id),
    UNIQUE(message_id, attachment_id)
);

CREATE TABLE imessage_messages (
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
    occurred_at_utc TEXT,
    source_updated_at_utc TEXT,
    content_complete INTEGER NOT NULL DEFAULT 1 CHECK(content_complete IN (0, 1)),
    ingestion_completeness TEXT NOT NULL DEFAULT 'complete'
        CHECK(ingestion_completeness IN ('metadata', 'partial', 'complete')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE imessage_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES imessage_messages(id),
    reactor_handle_id INTEGER NOT NULL REFERENCES imessage_handles(id),
    reaction_type INTEGER NOT NULL,
    apple_associated_guid TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(message_id, reactor_handle_id)
);

CREATE TABLE imessage_sync_cursor (
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

CREATE TABLE ingestion_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    source TEXT NOT NULL,
    job_kind TEXT NOT NULL DEFAULT 'sync',
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'retry', 'done', 'dead', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK(max_attempts >= 1),
    available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_token TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE ingestion_source_state (
    source TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle'
        CHECK(status IN ('idle', 'running', 'degraded', 'error', 'disabled')),
    cursor_json TEXT NOT NULL DEFAULT '{}',
    coverage_start_utc TEXT,
    coverage_end_utc TEXT,
    completeness TEXT NOT NULL DEFAULT 'unknown'
        CHECK(completeness IN ('unknown', 'partial', 'complete')),
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_item_at TEXT,
    item_count INTEGER NOT NULL DEFAULT 0 CHECK(item_count >= 0),
    heartbeat_at TEXT,
    error_code TEXT,
    error_message TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE jarvis_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            entry TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE knowledge_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_item_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(knowledge_item_id, model)
);

CREATE TABLE knowledge_index_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT 'upsert'
        CHECK(operation IN ('upsert', 'delete')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'retry', 'done', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT,
    last_error_code TEXT,
    claimed_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_id)
);

CREATE TABLE knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0 CHECK(chunk_index >= 0),
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    searchable_text TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    people_json TEXT NOT NULL DEFAULT '[]',
    occurred_at TEXT,
    source_updated_at TEXT,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content_hash TEXT NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    cloud_policy TEXT NOT NULL DEFAULT 'redact',
    trust TEXT NOT NULL DEFAULT 'untrusted_stored_data',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    deleted_at TEXT,
    UNIQUE(source_type, source_id, chunk_index)
);

CREATE VIRTUAL TABLE knowledge_items_fts USING fts5(
                title,
                searchable_text,
                summary,
                content='knowledge_items', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

CREATE TABLE knowledge_retrieval_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0 CHECK(rank >= 0),
    referenced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, uid)
);

CREATE TABLE knowledge_source_state (
    source_key TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok'
        CHECK(status IN ('ok', 'degraded', 'unavailable')),
    cursor TEXT,
    item_count INTEGER NOT NULL DEFAULT 0 CHECK(item_count >= 0),
    last_indexed_at TEXT,
    last_backfill_at TEXT,
    last_error_code TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE life_context (
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

CREATE TABLE life_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,       -- values, goals, fears, patterns, strengths
    content TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE llm_action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    agent TEXT,
    action_type TEXT,
    payload TEXT,
    status TEXT CHECK(status IN ('success', 'error', 'pending')),
    execution_time_ms INTEGER
);

CREATE TABLE location_history (
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

CREATE TABLE location_patterns (
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

CREATE TABLE location_point_dedup (
            device_id TEXT NOT NULL,
            client_point_id TEXT NOT NULL,
            location_history_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (device_id, client_point_id)
        );

CREATE TABLE meals (
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
        , protein_g REAL CHECK(protein_g IS NULL OR protein_g >= 0), carbs_g REAL CHECK(carbs_g IS NULL OR carbs_g >= 0), fat_g REAL CHECK(fat_g IS NULL OR fat_g >= 0), fiber_g REAL CHECK(fiber_g IS NULL OR fiber_g >= 0), items_json TEXT, photo_path TEXT, analysis_source TEXT NOT NULL DEFAULT 'manual' CHECK(analysis_source IN ('manual', 'text_ai', 'photo_ai')), confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), raw_input TEXT);

CREATE TABLE memory_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL CHECK(source_type IN ('recording', 'episode')),
            source_id INTEGER NOT NULL,
            text_preview TEXT,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_type, source_id)
        );

CREATE TABLE message_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            since_message_id INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            acknowledged INTEGER DEFAULT 0
        );

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER REFERENCES conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    agent TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost REAL,
    usage_estimated INTEGER NOT NULL DEFAULT 0 CHECK(usage_estimated IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
                content,
                content='messages', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

CREATE TABLE metric_samples (
    metric TEXT NOT NULL,
    bucket_at DATETIME NOT NULL,
    value REAL NOT NULL,
    last_value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    sample_count INTEGER NOT NULL DEFAULT 1 CHECK(sample_count >= 1),
    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(metric, bucket_at)
);

CREATE TABLE mobile_chat_dedup (
            device_id TEXT NOT NULL,
            client_message_id TEXT NOT NULL,
            conversation_id INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (device_id, client_message_id)
        );

CREATE TABLE mobile_devices (
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
        );

CREATE TABLE mobile_pairing_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT UNIQUE NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_at DATETIME
        );

CREATE TABLE mood_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mood_score INTEGER CHECK(mood_score BETWEEN 1 AND 10),
    energy_level INTEGER CHECK(energy_level BETWEEN 1 AND 10),
    context TEXT,
    triggers TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mood_signals (
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
        );

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,          -- email, pattern, calendar, system…
    title TEXT NOT NULL,
    content TEXT,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('urgent', 'high', 'medium', 'low')),
    read BOOLEAN DEFAULT 0,
    email_id TEXT,                 -- lien vers gmail_id si source=email
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT,
    description TEXT NOT NULL,
    occurrences INTEGER DEFAULT 1,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'resolved', 'monitoring'))
);

CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    relationship TEXT,
    personality_notes TEXT,
    dynamics TEXT,
    patterns TEXT,
    last_mentioned DATETIME,
    ai_description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
, imessage_count INTEGER DEFAULT 0, timeline_cache TEXT, timeline_updated_at DATETIME, birthday TEXT, running_gags TEXT);

CREATE TABLE people_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
    event_type TEXT,
    content TEXT NOT NULL,
    lesson_learned TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE perf_benchmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            commit_sha TEXT,
            duration_ms REAL NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE places (
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

CREATE TABLE presence_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arrived_at DATETIME NOT NULL,
            left_at DATETIME,
            duration_min REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE recording_sessions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    label TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'capturing'
        CHECK(state IN ('capturing', 'queued', 'ready', 'processing', 'retry', 'partial', 'completed', 'failed', 'expired')),
    spool_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
    checksum TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    error TEXT,
    transcript TEXT,
    summary TEXT,
    desktop_notification_claimed_at TEXT,
    retention_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_session_id TEXT REFERENCES recording_sessions(id) ON DELETE SET NULL,
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

CREATE TABLE relationship_events (
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

CREATE TABLE relationship_profiles (
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

CREATE TABLE scheduler_job_runs (
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
);

CREATE TABLE schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            checksum TEXT NOT NULL,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE school_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER REFERENCES school_subjects(id),
    title TEXT NOT NULL,
    content TEXT,
    doc_type TEXT,
    file_path TEXT,
    embedding BLOB,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE school_flashcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER REFERENCES school_subjects(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    next_review DATETIME DEFAULT CURRENT_TIMESTAMP,
    ease_factor REAL DEFAULT 2.5,
    interval_days INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE school_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    teacher TEXT,
    schedule TEXT,
    notes TEXT
);

CREATE TABLE screen_activity (
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

CREATE TABLE security_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            rule TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('high', 'medium', 'low')),
            snippet TEXT,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'fixed', 'ignored')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file, line, rule)
        );

CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_agent TEXT,
            ip TEXT,
            revoked INTEGER DEFAULT 0
        , mobile_device_id TEXT);

CREATE TABLE sync_entity_versions (
    entity_key TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    checksum TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sync_operations (
    operation_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    base_version INTEGER,
    resolved_version INTEGER NOT NULL,
    status_code INTEGER NOT NULL,
    response_body BLOB,
    response_content_type TEXT,
    response_headers_json TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tasks (
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

CREATE TABLE trips (
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

CREATE TABLE user_facts (
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

CREATE TABLE user_profiles (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 80),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME
        );

CREATE TABLE visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL REFERENCES places(id),
    arrived_at DATETIME NOT NULL,
    departed_at DATETIME,
    duration_min REAL,
    day_of_week INTEGER,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE voice_debug_log (
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
);

CREATE TABLE water_intake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount_ml INTEGER NOT NULL CHECK(amount_ml > 0),
            source TEXT NOT NULL CHECK(source IN ('voice', 'pwa')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

CREATE TABLE weekly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start DATE,
    summary TEXT,
    patterns_spotted TEXT,
    recommendations TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE wellbeing_logs (
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

CREATE TABLE work_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    app TEXT,
    started_at DATETIME NOT NULL,
    ended_at DATETIME,
    duration_min REAL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workouts (
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

CREATE INDEX idx_agent_approval_outbox_pending
    ON agent_approval_outbox(profile_id, run_id, status, created_at);

CREATE UNIQUE INDEX idx_agent_approvals_profile_decision
    ON agent_approvals(profile_id, decision_id)
    WHERE decision_id IS NOT NULL;

CREATE INDEX idx_agent_approvals_profile_run
    ON agent_approvals(profile_id, run_id, decision);

CREATE INDEX idx_agent_artifacts_profile_run
    ON agent_artifacts(profile_id, run_id, created_at);

CREATE INDEX idx_agent_checkpoints_profile_run
    ON agent_checkpoints(profile_id, run_id, sequence DESC);

CREATE INDEX idx_agent_event_inbox_pending
    ON agent_event_inbox(profile_id, processed_at, processing_started_at, created_at);

CREATE UNIQUE INDEX idx_agent_events_external
    ON agent_events(run_id, external_event_id)
    WHERE external_event_id IS NOT NULL;

CREATE INDEX idx_agent_events_profile_run
    ON agent_events(profile_id, run_id, sequence);

CREATE INDEX idx_agent_grants_profile_run
    ON agent_capability_grants(profile_id, run_id, revoked_at);

CREATE INDEX idx_agent_metrics_profile_run
    ON agent_metrics(profile_id, run_id, recorded_at);

CREATE INDEX idx_agent_runs_conversation
    ON agent_runs(profile_id, conversation_id);

CREATE UNIQUE INDEX idx_agent_runs_profile_idempotency
    ON agent_runs(profile_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_agent_runs_profile_status
    ON agent_runs(profile_id, status, created_at DESC);

CREATE INDEX idx_agent_runs_task ON agent_runs(profile_id, task_id);

CREATE INDEX idx_agent_steps_profile_run
    ON agent_steps(profile_id, run_id, sequence);

CREATE INDEX idx_agentic_conv ON agentic_workflows(conversation_id);

CREATE INDEX idx_agentic_status ON agentic_workflows(status);

CREATE INDEX idx_apple_shortcut_registry_enabled
    ON apple_shortcut_registry(enabled, lower(name));

CREATE INDEX idx_apple_shortcut_runs_created
    ON apple_shortcut_runs(created_at DESC);

CREATE INDEX idx_appusage_date ON app_usage(date);

CREATE INDEX idx_auth_rate_limits_updated ON auth_rate_limits(updated_at);

CREATE INDEX idx_calendar_events_start
    ON calendar_events(start_at, end_at);

CREATE INDEX idx_commitments_status ON commitments(status);

CREATE INDEX idx_connector_bindings_enabled
    ON connector_bindings(enabled, source);

CREATE INDEX idx_contact_identities_person
    ON contact_identities(person_id, identity_type);

CREATE INDEX idx_control_task_activity_task
    ON control_task_activity(profile_id, task_id, sequence);

CREATE INDEX idx_control_task_candidates_decision
    ON control_task_candidates(profile_id, decision, created_at DESC);

CREATE UNIQUE INDEX idx_control_task_candidates_dedupe
    ON control_task_candidates(profile_id, dedupe_key)
    WHERE dedupe_key <> '';

CREATE INDEX idx_control_task_comments_task
    ON control_task_comments(profile_id, task_id, created_at);

CREATE INDEX idx_control_task_plans_task
    ON control_task_plans(profile_id, task_id, version DESC);

CREATE UNIQUE INDEX idx_control_tasks_legacy
    ON control_tasks(profile_id, legacy_task_id)
    WHERE legacy_task_id IS NOT NULL;

CREATE INDEX idx_control_tasks_profile_status
    ON control_tasks(profile_id, status, updated_at DESC);

CREATE INDEX idx_control_tasks_run
    ON control_tasks(agentic_run_id);

CREATE INDEX idx_convdocs_conv ON conversation_documents(conversation_id);

CREATE UNIQUE INDEX idx_conversations_checkpoint_id ON conversations(checkpoint_id) WHERE checkpoint_id IS NOT NULL;

CREATE INDEX idx_crossinsights_type ON cross_insights(insight_type);

CREATE INDEX idx_cursor_jobs_created ON cursor_delegation_jobs(created_at);

CREATE INDEX idx_cursor_jobs_status ON cursor_delegation_jobs(status);

CREATE INDEX idx_dev_deployments_project ON dev_deployments(project_id, created_at DESC);

CREATE INDEX idx_dev_loop_log_project ON dev_loop_log(project_id, created_at DESC);

CREATE INDEX idx_dev_projects_status ON dev_projects(status);

CREATE UNIQUE INDEX idx_devices_id ON devices(device_id);

CREATE UNIQUE INDEX idx_devices_token_hash
           ON devices(token_hash) WHERE token_hash IS NOT NULL;

CREATE INDEX idx_email_summaries_gmail ON email_summaries(gmail_id);

CREATE INDEX idx_episodes_agent ON episodes(agent);

CREATE INDEX idx_episodes_created ON episodes(created_at);

CREATE UNIQUE INDEX idx_episodes_recording_unique
            ON episodes(recording_id)
            WHERE recording_id IS NOT NULL;

CREATE INDEX idx_event_log_timestamp ON event_log(timestamp);

CREATE INDEX idx_event_log_type ON event_log(event_type);

CREATE INDEX idx_facts_category ON user_facts(category);

CREATE INDEX idx_facts_current ON user_facts(is_current);

CREATE INDEX idx_fitness_program_active ON fitness_programs(active);

CREATE INDEX idx_fitness_progress_date ON fitness_session_progress(date, status);

CREATE INDEX idx_fitness_prompt_date ON fitness_prompt_log(date, kind);

CREATE INDEX idx_fitness_sessions_day ON fitness_program_sessions(program_id, day_of_week);

CREATE INDEX idx_fitness_weight_date ON fitness_weight_logs(date);

CREATE INDEX idx_flashcards_review ON school_flashcards(next_review);

CREATE INDEX idx_food_menu_restaurant
    ON food_menu_cache(restaurant, available);

CREATE INDEX idx_food_orders_created
    ON food_orders(created_at DESC);

CREATE INDEX idx_food_orders_delivery
    ON food_orders(delivery_status) WHERE delivery_status IS NOT NULL;

CREATE UNIQUE INDEX idx_food_orders_placed_plan
    ON food_orders(plan_id) WHERE status = 'placed' AND plan_id IS NOT NULL;

CREATE INDEX idx_food_orders_status_created
    ON food_orders(status, created_at DESC);

CREATE INDEX idx_food_suggestions_active
    ON food_suggestions(ordered, expires_at, slot);

CREATE UNIQUE INDEX idx_imcache_handle ON imessage_analysis_cache(handle);

CREATE UNIQUE INDEX idx_imessage_att_apple ON imessage_attachments(apple_attachment_id);

CREATE UNIQUE INDEX idx_imessage_att_guid ON imessage_attachments(guid);

CREATE INDEX idx_imessage_ch_chat ON imessage_chat_handles(chat_id);

CREATE INDEX idx_imessage_ch_handle ON imessage_chat_handles(handle_id);

CREATE UNIQUE INDEX idx_imessage_chats_apple ON imessage_chats(apple_chat_id);

CREATE INDEX idx_imessage_chats_identifier ON imessage_chats(chat_identifier);

CREATE UNIQUE INDEX idx_imessage_handles_apple ON imessage_handles(apple_handle_id);

CREATE INDEX idx_imessage_handles_value ON imessage_handles(handle);

CREATE INDEX idx_imessage_ma_att ON imessage_message_attachments(attachment_id);

CREATE INDEX idx_imessage_ma_msg ON imessage_message_attachments(message_id);

CREATE INDEX idx_imessage_msg_associated ON imessage_messages(associated_message_guid);

CREATE INDEX idx_imessage_msg_chat ON imessage_messages(chat_id);

CREATE INDEX idx_imessage_msg_date ON imessage_messages(date);

CREATE UNIQUE INDEX idx_imessage_msg_guid ON imessage_messages(guid);

CREATE INDEX idx_imessage_msg_handle ON imessage_messages(handle_id);

CREATE UNIQUE INDEX idx_imessage_msg_hash ON imessage_messages(content_hash);

CREATE UNIQUE INDEX idx_imessage_msg_rowid ON imessage_messages(apple_rowid);

CREATE INDEX idx_imessage_reactions_msg ON imessage_reactions(message_id);

CREATE UNIQUE INDEX idx_ingestion_jobs_active_dedupe
    ON ingestion_jobs(source, job_kind, dedupe_key)
    WHERE status IN ('pending', 'running', 'retry');

CREATE INDEX idx_ingestion_jobs_claim
    ON ingestion_jobs(status, available_at, lease_expires_at, id);

CREATE INDEX idx_ingestion_source_health
    ON ingestion_source_state(status, last_success_at);

CREATE INDEX idx_knowledge_embeddings_hash
    ON knowledge_embeddings(content_hash);

CREATE INDEX idx_knowledge_items_conversation
    ON knowledge_items(conversation_id, occurred_at DESC);

CREATE INDEX idx_knowledge_items_hash
    ON knowledge_items(content_hash);

CREATE INDEX idx_knowledge_items_source_time
    ON knowledge_items(source_type, occurred_at DESC);

CREATE INDEX idx_knowledge_jobs_pending
    ON knowledge_index_jobs(status, next_attempt_at, created_at);

CREATE INDEX idx_knowledge_references_conversation
    ON knowledge_retrieval_references(conversation_id, referenced_at DESC, rank);

CREATE INDEX idx_knowledge_source_state_type
    ON knowledge_source_state(source_type, status);

CREATE INDEX idx_lifecontext_active ON life_context(active);

CREATE INDEX idx_llm_logs_action_type ON llm_action_logs(action_type);

CREATE INDEX idx_llm_logs_created ON llm_action_logs(created_at);

CREATE INDEX idx_location_date ON location_history(created_at);

CREATE INDEX idx_location_place ON location_history(place_id);

CREATE INDEX idx_location_point_dedup_created ON location_point_dedup(created_at);

CREATE INDEX idx_meals_date ON meals(date);

CREATE INDEX idx_messages_conv ON messages(conversation_id);

CREATE INDEX idx_messages_created ON messages(created_at);

CREATE INDEX idx_metric_samples_recorded
    ON metric_samples(recorded_at);

CREATE INDEX idx_mobile_chat_dedup_created ON mobile_chat_dedup(created_at);

CREATE INDEX idx_mobile_fcm_token ON mobile_devices(fcm_token);

CREATE INDEX idx_mobile_token_hash ON mobile_devices(token_hash);

CREATE INDEX idx_mood_created ON mood_log(created_at);

CREATE INDEX idx_notif_created ON notifications(created_at);

CREATE INDEX idx_notif_dedup ON notifications(source, title, email_id, created_at DESC);

CREATE INDEX idx_notif_read ON notifications(read);

CREATE INDEX idx_people_name ON people(name);

CREATE INDEX idx_perf_scope ON perf_benchmarks(scope, created_at DESC);

CREATE INDEX idx_places_category ON places(category);

CREATE INDEX idx_places_name ON places(name);

CREATE INDEX idx_presence_arrived ON presence_sessions(arrived_at);

CREATE INDEX idx_recording_sessions_due
    ON recording_sessions(state, retention_until, updated_at);

CREATE INDEX idx_recordings_date ON recordings(created_at);

CREATE UNIQUE INDEX idx_recordings_session_unique
            ON recordings(recording_session_id)
            WHERE recording_session_id IS NOT NULL;

CREATE INDEX idx_relevents_date ON relationship_events(event_date);

CREATE INDEX idx_relevents_person ON relationship_events(person_id);

CREATE INDEX idx_relprofile_person ON relationship_profiles(person_id);

CREATE INDEX idx_scheduler_runs_job_started
    ON scheduler_job_runs(job_id, started_at DESC);

CREATE INDEX idx_scheduler_runs_started
    ON scheduler_job_runs(started_at DESC);

CREATE INDEX idx_screen_app ON screen_activity(app);

CREATE INDEX idx_screen_date ON screen_activity(created_at);

CREATE INDEX idx_screen_device ON screen_activity(device);

CREATE INDEX idx_sessions_mobile_device ON sessions(mobile_device_id);

CREATE INDEX idx_sessions_token_hash ON sessions(token_hash);

CREATE INDEX idx_sync_operations_entity
    ON sync_operations(entity_key, created_at);

CREATE INDEX idx_tasks_status ON tasks(status);

CREATE INDEX idx_trips_date ON trips(started_at);

CREATE INDEX idx_turns_recording ON conversation_turns(recording_id);

CREATE UNIQUE INDEX idx_turns_recording_order_unique
            ON conversation_turns(recording_id, turn_order);

CREATE INDEX idx_user_profiles_active
            ON user_profiles(is_active, display_name);

CREATE INDEX idx_vdebug_created ON voice_debug_log(created_at);

CREATE INDEX idx_visits_date ON visits(arrived_at);

CREATE INDEX idx_visits_day ON visits(day_of_week);

CREATE INDEX idx_visits_place ON visits(place_id);

CREATE INDEX idx_water_date ON water_intake(date);

CREATE INDEX idx_wellbeing_date ON wellbeing_logs(date);

CREATE INDEX idx_workouts_date ON workouts(date);

CREATE INDEX idx_worksessions_date ON work_sessions(started_at);

CREATE TRIGGER knowledge_items_fts_ad
            AFTER DELETE ON knowledge_items BEGIN
                INSERT INTO knowledge_items_fts(
                    knowledge_items_fts, rowid, title, searchable_text, summary
                ) VALUES (
                    'delete', old.id, old.title, old.searchable_text, old.summary
                );
            END;

CREATE TRIGGER knowledge_items_fts_ai
            AFTER INSERT ON knowledge_items BEGIN
                INSERT INTO knowledge_items_fts(rowid, title, searchable_text, summary)
                VALUES (new.id, new.title, new.searchable_text, new.summary);
            END;

CREATE TRIGGER knowledge_items_fts_au
            AFTER UPDATE OF title, searchable_text, summary ON knowledge_items BEGIN
                INSERT INTO knowledge_items_fts(
                    knowledge_items_fts, rowid, title, searchable_text, summary
                ) VALUES (
                    'delete', old.id, old.title, old.searchable_text, old.summary
                );
                INSERT INTO knowledge_items_fts(rowid, title, searchable_text, summary)
                VALUES (new.id, new.title, new.searchable_text, new.summary);
            END;

CREATE TRIGGER knowledge_job_agent_approvals_agent_approval_ad
            AFTER DELETE ON agent_approvals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_approval', CAST(old.approval_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_approvals_agent_approval_ai
            AFTER INSERT ON agent_approvals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_approval', CAST(new.approval_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_approvals_agent_approval_au
            AFTER UPDATE ON agent_approvals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_approval', CAST(new.approval_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_artifacts_agent_artifact_ad
            AFTER DELETE ON agent_artifacts BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_artifact', CAST(old.artifact_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_artifacts_agent_artifact_ai
            AFTER INSERT ON agent_artifacts BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_artifact', CAST(new.artifact_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_artifacts_agent_artifact_au
            AFTER UPDATE ON agent_artifacts BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_artifact', CAST(new.artifact_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_runs_agent_run_ad
            AFTER DELETE ON agent_runs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_run', CAST(old.run_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_runs_agent_run_ai
            AFTER INSERT ON agent_runs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_run', CAST(new.run_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_runs_agent_run_au
            AFTER UPDATE ON agent_runs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_run', CAST(new.run_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_steps_agent_step_ad
            AFTER DELETE ON agent_steps BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_step', CAST(old.step_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_steps_agent_step_ai
            AFTER INSERT ON agent_steps BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_step', CAST(new.step_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agent_steps_agent_step_au
            AFTER UPDATE ON agent_steps BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agent_step', CAST(new.step_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agentic_workflows_agentic_workflow_ad
            AFTER DELETE ON agentic_workflows BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agentic_workflow', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agentic_workflows_agentic_workflow_ai
            AFTER INSERT ON agentic_workflows BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agentic_workflow', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_agentic_workflows_agentic_workflow_au
            AFTER UPDATE ON agentic_workflows BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'agentic_workflow', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_calendar_events_calendar_ad
            AFTER DELETE ON calendar_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'calendar', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_calendar_events_calendar_ai
            AFTER INSERT ON calendar_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'calendar', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_calendar_events_calendar_au
            AFTER UPDATE ON calendar_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'calendar', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_commitments_commitment_ad
            AFTER DELETE ON commitments BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'commitment', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_commitments_commitment_ai
            AFTER INSERT ON commitments BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'commitment', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_commitments_commitment_au
            AFTER UPDATE ON commitments BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'commitment', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_activity_control_activity_ad
            AFTER DELETE ON control_task_activity BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_activity', CAST(old.activity_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_activity_control_activity_ai
            AFTER INSERT ON control_task_activity BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_activity', CAST(new.activity_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_activity_control_activity_au
            AFTER UPDATE ON control_task_activity BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_activity', CAST(new.activity_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_comments_control_comment_ad
            AFTER DELETE ON control_task_comments BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_comment', CAST(old.comment_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_comments_control_comment_ai
            AFTER INSERT ON control_task_comments BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_comment', CAST(new.comment_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_comments_control_comment_au
            AFTER UPDATE ON control_task_comments BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_comment', CAST(new.comment_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_plans_control_plan_ad
            AFTER DELETE ON control_task_plans BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_plan', CAST(old.plan_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_plans_control_plan_ai
            AFTER INSERT ON control_task_plans BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_plan', CAST(new.plan_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_plans_control_plan_au
            AFTER UPDATE ON control_task_plans BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_plan', CAST(new.plan_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_reports_control_report_ad
            AFTER DELETE ON control_task_reports BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_report', CAST(old.report_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_reports_control_report_ai
            AFTER INSERT ON control_task_reports BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_report', CAST(new.report_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_task_reports_control_report_au
            AFTER UPDATE ON control_task_reports BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_report', CAST(new.report_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_tasks_control_task_ad
            AFTER DELETE ON control_tasks BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_task', CAST(old.task_id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_tasks_control_task_ai
            AFTER INSERT ON control_tasks BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_task', CAST(new.task_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_control_tasks_control_task_au
            AFTER UPDATE ON control_tasks BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'control_task', CAST(new.task_id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_documents_conversation_document_ad
            AFTER DELETE ON conversation_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation_document', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_documents_conversation_document_ai
            AFTER INSERT ON conversation_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation_document', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_documents_conversation_document_au
            AFTER UPDATE ON conversation_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation_document', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_documents_document_ad
            AFTER DELETE ON conversation_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'document', CAST('conversation:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_documents_document_ai
            AFTER INSERT ON conversation_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'document', CAST('conversation:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_documents_document_au
            AFTER UPDATE ON conversation_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'document', CAST('conversation:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_turns_conversation_turn_ad
            AFTER DELETE ON conversation_turns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation_turn', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_turns_conversation_turn_ai
            AFTER INSERT ON conversation_turns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation_turn', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversation_turns_conversation_turn_au
            AFTER UPDATE ON conversation_turns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation_turn', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversations_conversation_ad
            AFTER DELETE ON conversations BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversations_conversation_ai
            AFTER INSERT ON conversations BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_conversations_conversation_au
            AFTER UPDATE ON conversations BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'conversation', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_cross_insights_insight_ad
            AFTER DELETE ON cross_insights BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'insight', CAST('cross:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_cross_insights_insight_ai
            AFTER INSERT ON cross_insights BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'insight', CAST('cross:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_cross_insights_insight_au
            AFTER UPDATE ON cross_insights BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'insight', CAST('cross:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_cursor_delegation_jobs_cursor_job_ad
            AFTER DELETE ON cursor_delegation_jobs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'cursor_job', CAST(COALESCE(old.job_id, CAST(old.id AS TEXT)) AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_cursor_delegation_jobs_cursor_job_ai
            AFTER INSERT ON cursor_delegation_jobs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'cursor_job', CAST(COALESCE(new.job_id, CAST(new.id AS TEXT)) AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_cursor_delegation_jobs_cursor_job_au
            AFTER UPDATE ON cursor_delegation_jobs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'cursor_job', CAST(COALESCE(new.job_id, CAST(new.id AS TEXT)) AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_daily_briefings_briefing_ad
            AFTER DELETE ON daily_briefings BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'briefing', CAST('daily:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_daily_briefings_briefing_ai
            AFTER INSERT ON daily_briefings BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'briefing', CAST('daily:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_daily_briefings_briefing_au
            AFTER UPDATE ON daily_briefings BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'briefing', CAST('daily:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_dev_projects_project_ad
            AFTER DELETE ON dev_projects BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'project', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_dev_projects_project_ai
            AFTER INSERT ON dev_projects BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'project', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_dev_projects_project_au
            AFTER UPDATE ON dev_projects BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'project', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_email_summaries_email_ad
            AFTER DELETE ON email_summaries BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'email', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_email_summaries_email_ai
            AFTER INSERT ON email_summaries BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'email', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_email_summaries_email_au
            AFTER UPDATE ON email_summaries BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'email', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_episodes_episode_ad
            AFTER DELETE ON episodes BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'episode', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_episodes_episode_ai
            AFTER INSERT ON episodes BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'episode', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_episodes_episode_au
            AFTER UPDATE ON episodes BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'episode', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_episodes_note_ad
            AFTER DELETE ON episodes BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'note', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_episodes_note_ai
            AFTER INSERT ON episodes BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'note', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_episodes_note_au
            AFTER UPDATE ON episodes BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'note', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_program_sessions_wellbeing_ad
            AFTER DELETE ON fitness_program_sessions BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-session:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_program_sessions_wellbeing_ai
            AFTER INSERT ON fitness_program_sessions BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-session:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_program_sessions_wellbeing_au
            AFTER UPDATE ON fitness_program_sessions BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-session:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_programs_wellbeing_ad
            AFTER DELETE ON fitness_programs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-program:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_programs_wellbeing_ai
            AFTER INSERT ON fitness_programs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-program:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_programs_wellbeing_au
            AFTER UPDATE ON fitness_programs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-program:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_session_progress_wellbeing_ad
            AFTER DELETE ON fitness_session_progress BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-progress:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_session_progress_wellbeing_ai
            AFTER INSERT ON fitness_session_progress BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-progress:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_session_progress_wellbeing_au
            AFTER UPDATE ON fitness_session_progress BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('fitness-progress:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_weight_logs_wellbeing_ad
            AFTER DELETE ON fitness_weight_logs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('weight:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_weight_logs_wellbeing_ai
            AFTER INSERT ON fitness_weight_logs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('weight:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_fitness_weight_logs_wellbeing_au
            AFTER UPDATE ON fitness_weight_logs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('weight:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_food_orders_wellbeing_ad
            AFTER DELETE ON food_orders BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('food-order:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_food_orders_wellbeing_ai
            AFTER INSERT ON food_orders BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('food-order:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_food_orders_wellbeing_au
            AFTER UPDATE ON food_orders BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('food-order:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_food_preferences_wellbeing_ad
            AFTER DELETE ON food_preferences BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('food-pref:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_food_preferences_wellbeing_ai
            AFTER INSERT ON food_preferences BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('food-pref:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_food_preferences_wellbeing_au
            AFTER UPDATE ON food_preferences BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('food-pref:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_message_attachments_ad
            AFTER DELETE ON imessage_message_attachments BEGIN
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(old.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_message_attachments_ai
            AFTER INSERT ON imessage_message_attachments BEGIN
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(new.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_message_attachments_au
            AFTER UPDATE ON imessage_message_attachments BEGIN
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(old.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(new.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_messages_imessage_ad
            AFTER DELETE ON imessage_messages BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'imessage', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_messages_imessage_ai
            AFTER INSERT ON imessage_messages BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'imessage', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_messages_imessage_au
            AFTER UPDATE ON imessage_messages BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'imessage', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_reactions_ad
            AFTER DELETE ON imessage_reactions BEGIN
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(old.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_reactions_ai
            AFTER INSERT ON imessage_reactions BEGIN
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(new.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_imessage_reactions_au
            AFTER UPDATE ON imessage_reactions BEGIN
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(old.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
                INSERT INTO knowledge_index_jobs(
                source_type, source_id, operation, status, attempts,
                next_attempt_at, last_error_code, claimed_at, completed_at,
                created_at, updated_at
            ) VALUES (
                'imessage', CAST(new.message_id AS TEXT), 'upsert',
                'pending', 0, NULL, NULL, NULL, NULL,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                operation = 'upsert', status = 'pending', attempts = 0,
                next_attempt_at = NULL, last_error_code = NULL,
                claimed_at = NULL, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_jarvis_journal_journal_ad
            AFTER DELETE ON jarvis_journal BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'journal', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_jarvis_journal_journal_ai
            AFTER INSERT ON jarvis_journal BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'journal', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_jarvis_journal_journal_au
            AFTER UPDATE ON jarvis_journal BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'journal', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_life_context_life_context_ad
            AFTER DELETE ON life_context BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'life_context', CAST('context:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_life_context_life_context_ai
            AFTER INSERT ON life_context BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'life_context', CAST('context:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_life_context_life_context_au
            AFTER UPDATE ON life_context BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'life_context', CAST('context:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_life_profile_life_context_ad
            AFTER DELETE ON life_profile BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'life_context', CAST('profile:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_life_profile_life_context_ai
            AFTER INSERT ON life_profile BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'life_context', CAST('profile:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_life_profile_life_context_au
            AFTER UPDATE ON life_profile BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'life_context', CAST('profile:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_location_patterns_location_ad
            AFTER DELETE ON location_patterns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'location', CAST('pattern:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_location_patterns_location_ai
            AFTER INSERT ON location_patterns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'location', CAST('pattern:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_location_patterns_location_au
            AFTER UPDATE ON location_patterns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'location', CAST('pattern:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_meals_wellbeing_ad
            AFTER DELETE ON meals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('meal:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_meals_wellbeing_ai
            AFTER INSERT ON meals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('meal:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_meals_wellbeing_au
            AFTER UPDATE ON meals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('meal:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_message_insights_insight_ad
            AFTER DELETE ON message_insights BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'insight', CAST('message:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_message_insights_insight_ai
            AFTER INSERT ON message_insights BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'insight', CAST('message:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_message_insights_insight_au
            AFTER UPDATE ON message_insights BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'insight', CAST('message:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_messages_message_ad
            AFTER DELETE ON messages BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'message', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_messages_message_ai
            AFTER INSERT ON messages BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'message', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_messages_message_au
            AFTER UPDATE ON messages BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'message', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_mood_log_wellbeing_ad
            AFTER DELETE ON mood_log BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('mood:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_mood_log_wellbeing_ai
            AFTER INSERT ON mood_log BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('mood:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_mood_log_wellbeing_au
            AFTER UPDATE ON mood_log BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('mood:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_mood_signals_wellbeing_ad
            AFTER DELETE ON mood_signals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('mood-signal:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_mood_signals_wellbeing_ai
            AFTER INSERT ON mood_signals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('mood-signal:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_mood_signals_wellbeing_au
            AFTER UPDATE ON mood_signals BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('mood-signal:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_notifications_notification_ad
            AFTER DELETE ON notifications BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'notification', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_notifications_notification_ai
            AFTER INSERT ON notifications BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'notification', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_notifications_notification_au
            AFTER UPDATE ON notifications BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'notification', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_patterns_pattern_ad
            AFTER DELETE ON patterns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'pattern', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_patterns_pattern_ai
            AFTER INSERT ON patterns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'pattern', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_patterns_pattern_au
            AFTER UPDATE ON patterns BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'pattern', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_people_events_people_event_ad
            AFTER DELETE ON people_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'people_event', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_people_events_people_event_ai
            AFTER INSERT ON people_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'people_event', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_people_events_people_event_au
            AFTER UPDATE ON people_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'people_event', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_people_person_ad
            AFTER DELETE ON people BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'person', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_people_person_ai
            AFTER INSERT ON people BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'person', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_people_person_au
            AFTER UPDATE ON people BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'person', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_places_location_ad
            AFTER DELETE ON places BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'location', CAST('place:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_places_location_ai
            AFTER INSERT ON places BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'location', CAST('place:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_places_location_au
            AFTER UPDATE ON places BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'location', CAST('place:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_recordings_recording_ad
            AFTER DELETE ON recordings BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'recording', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_recordings_recording_ai
            AFTER INSERT ON recordings BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'recording', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_recordings_recording_au
            AFTER UPDATE ON recordings BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'recording', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_relationship_events_relationship_event_ad
            AFTER DELETE ON relationship_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'relationship_event', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_relationship_events_relationship_event_ai
            AFTER INSERT ON relationship_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'relationship_event', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_relationship_events_relationship_event_au
            AFTER UPDATE ON relationship_events BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'relationship_event', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_relationship_profiles_relationship_ad
            AFTER DELETE ON relationship_profiles BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'relationship', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_relationship_profiles_relationship_ai
            AFTER INSERT ON relationship_profiles BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'relationship', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_relationship_profiles_relationship_au
            AFTER UPDATE ON relationship_profiles BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'relationship', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_scheduler_job_runs_scheduler_job_ad
            AFTER DELETE ON scheduler_job_runs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'scheduler_job', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_scheduler_job_runs_scheduler_job_ai
            AFTER INSERT ON scheduler_job_runs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'scheduler_job', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_scheduler_job_runs_scheduler_job_au
            AFTER UPDATE ON scheduler_job_runs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'scheduler_job', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_school_documents_document_ad
            AFTER DELETE ON school_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'document', CAST('school:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_school_documents_document_ai
            AFTER INSERT ON school_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'document', CAST('school:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_school_documents_document_au
            AFTER UPDATE ON school_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'document', CAST('school:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_school_documents_school_document_ad
            AFTER DELETE ON school_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'school_document', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_school_documents_school_document_ai
            AFTER INSERT ON school_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'school_document', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_school_documents_school_document_au
            AFTER UPDATE ON school_documents BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'school_document', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_tasks_task_ad
            AFTER DELETE ON tasks BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'task', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_tasks_task_ai
            AFTER INSERT ON tasks BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'task', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_tasks_task_au
            AFTER UPDATE ON tasks BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'task', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_user_facts_fact_ad
            AFTER DELETE ON user_facts BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'fact', CAST(old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_user_facts_fact_ai
            AFTER INSERT ON user_facts BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'fact', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_user_facts_fact_au
            AFTER UPDATE ON user_facts BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'fact', CAST(new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_weekly_summaries_briefing_ad
            AFTER DELETE ON weekly_summaries BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'briefing', CAST('weekly:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_weekly_summaries_briefing_ai
            AFTER INSERT ON weekly_summaries BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'briefing', CAST('weekly:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_weekly_summaries_briefing_au
            AFTER UPDATE ON weekly_summaries BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'briefing', CAST('weekly:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_wellbeing_logs_wellbeing_ad
            AFTER DELETE ON wellbeing_logs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('wellbeing:' || old.id AS TEXT), 'delete',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_wellbeing_logs_wellbeing_ai
            AFTER INSERT ON wellbeing_logs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('wellbeing:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER knowledge_job_wellbeing_logs_wellbeing_au
            AFTER UPDATE ON wellbeing_logs BEGIN
                INSERT INTO knowledge_index_jobs(
                    source_type, source_id, operation, status, attempts,
                    next_attempt_at, last_error_code, claimed_at, completed_at,
                    created_at, updated_at
                ) VALUES (
                    'wellbeing', CAST('wellbeing:' || new.id AS TEXT), 'upsert',
                    'pending', 0, NULL, NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    operation = excluded.operation,
                    status = 'pending',
                    attempts = 0,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    claimed_at = NULL,
                    completed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            END;

CREATE TRIGGER memory_embeddings_episode_ad
        AFTER DELETE ON episodes BEGIN
            DELETE FROM memory_embeddings
            WHERE source_type = 'episode' AND source_id = CAST(old.id AS TEXT);
        END;

CREATE TRIGGER memory_embeddings_episode_au
        AFTER UPDATE ON episodes BEGIN
            DELETE FROM memory_embeddings
            WHERE source_type = 'episode' AND source_id = CAST(old.id AS TEXT);
        END;

CREATE TRIGGER memory_embeddings_recording_ad
        AFTER DELETE ON recordings BEGIN
            DELETE FROM memory_embeddings
            WHERE source_type = 'recording' AND source_id = CAST(old.id AS TEXT);
        END;

CREATE TRIGGER memory_embeddings_recording_au
        AFTER UPDATE ON recordings BEGIN
            DELETE FROM memory_embeddings
            WHERE source_type = 'recording' AND source_id = CAST(old.id AS TEXT);
        END;

CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;

CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;

CREATE TRIGGER messages_fts_au AFTER UPDATE OF content ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
