"""Façade rétrocompatible de la couche de persistance JARVIS.

Les implémentations vivent dans des modules par domaine. Les réexports ci-dessous
préservent l'API historique ``from database import ...``.
"""

from __future__ import annotations

from pathlib import Path

import config

DB_PATH = Path(config.DB_PATH)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

from .schema import SCHEMA
from .core import (
    build_full_context,
    count_memory_stats,
    get_connection,
    get_db,
    get_usage_stats,
    init_db,
)


from .conversations import (
    create_agentic_workflow,
    create_conversation,
    delete_conversation,
    end_conversation,
    get_conversation_detail,
    get_conversation_documents,
    get_conversation_history,
    get_conversations,
    get_last_conversation_summary,
    get_messages_since,
    save_conversation_document,
    save_message,
    save_message_insight,
    search_conversations,
    update_agentic_workflow,
    update_conversation,
    update_conversation_activity,
)

from .episodes import (
    get_recent_episodes,
    get_recording,
    get_recordings,
    get_weekly_episodes,
    save_episode,
    save_recording,
    save_weekly_summary,
    _dispatch_semantic_indexing,
)

from .people import (
    add_life_context,
    add_life_profile_entry,
    add_people_event,
    clear_person_ai_description,
    close_life_context,
    delete_life_profile_entry,
    force_upsert_people_from_mac_sync,
    get_active_life_context,
    get_all_life_context,
    get_all_people,
    get_analysis_cursor,
    get_life_profile,
    get_life_profile_entries,
    get_people_sorted_by_recent,
    get_person,
    get_person_timeline_cache,
    get_total_messages_analyzed,
    patch_person,
    rename_person_if_phone_number,
    set_person_ai_description,
    sync_imessage_counts_to_people,
    update_analysis_cursor,
    update_life_profile_entry,
    update_person_imessage_count,
    update_person_timeline_cache,
    upsert_person,
)

from .patterns import (
    create_pattern,
    find_or_create_pattern,
    get_active_patterns,
    get_daily_messages,
    get_pattern,
    get_recent_moods,
    save_daily_briefing,
    save_mood,
    update_pattern,
)

from .school import (
    get_school_documents,
    save_school_document,
)

from .notifications import (
    create_notification,
    get_llm_logs,
    get_recent_notifications,
    get_unread_notifications,
    log_llm_action,
    mark_all_notifications_read,
    mark_notification_read,
    _dispatch_push_notification,
)

from .rituals import (
    add_commitment,
    add_running_gag,
    clear_dnd,
    close_presence_session,
    get_commitments,
    get_daily_ritual,
    get_day_score,
    get_dnd_status,
    get_jarvis_journal_entries,
    get_jarvis_journal_entry,
    get_mood_signals,
    get_overdue_commitments,
    get_running_gags,
    get_todays_birthdays,
    get_top_days,
    get_week_comparison,
    is_dnd_active,
    open_presence_session,
    set_daily_ritual,
    set_dnd,
    update_commitment_status,
    upsert_day_score,
    upsert_jarvis_journal_entry,
    upsert_mood_signal,
)

from .devops import (
    get_applied_migrations,
    get_duplicate_findings,
    get_perf_baseline,
    get_perf_history,
    get_security_findings,
    get_voice_debug_logs,
    record_migration,
    record_perf_benchmark,
    update_security_finding_status,
    upsert_duplicate_finding,
    upsert_security_finding,
    _save_voice_debug_trace,
)

# Réexports rétrocompatibles des premiers domaines extraits en Phase 2.
from .settings import get_setting, set_setting
from .tasks import (
    create_task,
    delete_all_tasks,
    delete_task,
    get_task,
    get_tasks,
    update_task_status,
)
from .sessions import (
    create_session_row,
    get_session_by_token_hash,
    list_active_sessions,
    purge_expired_sessions,
    revoke_all_sessions,
    revoke_session_by_id,
    revoke_session_by_token_hash,
    touch_session,
)
from .push import (
    delete_push_subscription,
    get_all_push_subscriptions,
    upsert_push_subscription,
)
from .conversation_turns import (
    assign_speaker_to_person,
    get_conversation_turns,
    get_unlabeled_speakers,
    save_conversation_turns,
)
from .embeddings import get_all_memory_embeddings, upsert_memory_embedding
from .email import (
    get_all_processed_email_ids,
    get_email_stats,
    get_processed_email_ids,
    get_recent_email_summaries,
    get_recent_emails_from_db,
    get_unread_emails_from_db,
    mark_email_read,
    save_email_full,
    upsert_email_summary,
)
from .facts import (
    add_fact,
    get_all_facts_summary,
    get_facts,
    invalidate_fact,
    search_facts,
)
from .relationships import (
    add_cross_insight,
    add_relationship_event,
    get_active_insights,
    get_all_relationship_profiles,
    get_relationship_profile,
    get_relationship_timeline,
    increment_insight,
    upsert_relationship_profile,
)
from .stats import get_cost_summary, get_daily_activity_stats
from .screen_daemon import (
    end_work_session,
    get_active_device,
    get_all_devices,
    get_app_usage,
    get_app_usage_range,
    get_current_screen_context,
    get_device_by_id,
    get_screen_activity,
    get_work_sessions,
    mark_device_offline,
    register_device,
    save_screen_activity,
    set_active_device,
    start_work_session,
    update_device_heartbeat,
    upsert_app_usage,
)
