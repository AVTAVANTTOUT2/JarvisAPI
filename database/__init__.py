"""Façade rétrocompatible de la couche de persistance JARVIS.

Les implémentations vivent dans des modules par domaine. Les réexports ci-dessous
préservent l'API historique ``from database import ...``.
"""

from __future__ import annotations

from pathlib import Path

import config
from core.file_security import ensure_private_directory

DB_PATH = Path(config.DB_PATH)
if str(DB_PATH) != ":memory:":
    ensure_private_directory(DB_PATH.parent)

from .schema import SCHEMA
from .core import (
    DEFAULT_PROFILE_ID,
    activate_profile,
    build_full_context,
    count_memory_stats,
    current_profile_id,
    db_transaction,
    get_connection,
    get_db,
    get_usage_stats,
    init_db,
    normalize_profile_id,
    profile_database_path,
    profile_storage_path,
    reset_profile,
    use_profile,
)
from .encryption import (
    DatabaseEncryptionError,
    database_encryption_status,
    disable_database_encryption,
    enable_database_encryption,
    export_plaintext_snapshot,
    replace_database_from_plaintext,
)
from .event_log import EventReplayWindow, get_event_log, get_event_replay_window
from .knowledge import (
    get_cached_calendar_events,
    get_knowledge_observability,
    get_recent_knowledge_references,
    save_knowledge_references,
    upsert_calendar_events,
)


from .conversations import (
    create_agentic_workflow,
    create_conversation,
    delete_conversation,
    end_conversation,
    get_conversation_detail,
    get_conversation_by_checkpoint,
    get_conversation_documents,
    get_conversation_history,
    get_conversations,
    normalize_checkpoint_id,
    resolve_conversation_checkpoint,
    get_last_conversation_summary,
    get_messages_since,
    save_conversation_document,
    save_message,
    save_message_insight,
    search_conversations,
    update_agentic_workflow,
    update_conversation,
    update_conversation_activity,
    update_generated_conversation_title,
)

from .unified_search import unified_search
from .metrics import get_metric_history, record_health_snapshot, record_metric_samples
from .profiles import (
    create_user_profile,
    deactivate_user_profile,
    list_user_profiles,
    profile_data_path,
    touch_user_profile,
    user_profile_exists,
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
    clear_person_timeline_cache,
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
    clear_llm_logs,
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
    get_voice_latency_metrics,
    record_migration,
    record_perf_benchmark,
    update_security_finding_status,
    update_voice_debug_latency,
    upsert_duplicate_finding,
    upsert_security_finding,
    _save_voice_debug_trace,
)

# Réexports rétrocompatibles des premiers domaines extraits en Phase 2.
from .settings import get_setting, set_setting
from .job_runs import (
    JobRunClaim,
    claim_job_run,
    complete_job_run,
    release_job_run,
)
from .tasks import (
    create_task,
    delete_all_tasks,
    delete_task,
    get_task,
    get_tasks,
    update_task_status,
)
from .sessions import (
    clear_all_auth_rate_limits,
    clear_auth_rate_limit,
    create_session_row,
    get_auth_rate_limit,
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
from .mobile import (
    clear_mobile_push_token,
    consume_mobile_pairing_code,
    create_mobile_pairing_code,
    get_active_mobile_push_tokens,
    get_mobile_chat_dedup,
    get_mobile_device_by_token_hash,
    list_mobile_devices,
    revoke_mobile_device,
    save_mobile_chat_dedup,
    touch_mobile_device,
    update_mobile_capabilities,
    update_mobile_push_token,
    upsert_mobile_device,
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
from .food_intelligence import (
    claim_suggestion,
    get_active_suggestion_by_slot,
    get_active_suggestions,
    get_food_preferences,
    get_menu_items,
    get_menu_restaurants,
    release_suggestion,
    replace_menu_items,
    replace_suggestions,
    set_food_preference,
)
from .food_orders import (
    get_daily_food_order_stats,
    get_food_order,
    get_food_orders,
    get_orders_awaiting_delivery,
    get_rated_food_orders,
    rate_food_order,
    record_food_order,
    update_food_order_delivery,
)
from .apple_shortcuts import (
    delete_registered_shortcut,
    find_registered_shortcut,
    get_registered_shortcut,
    list_registered_shortcuts,
    list_shortcut_runs,
    record_shortcut_run,
    register_shortcut,
    update_registered_shortcut,
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
from .scheduler_runs import (
    aggregate_scheduler_runs,
    finish_run as finish_scheduler_run,
    get_scheduler_run,
    list_scheduler_runs,
    purge_scheduler_runs,
    start_run as start_scheduler_run,
)
from .stats import get_cost_summary, get_daily_activity_stats
from .screen_daemon import (
    consume_device_pairing_code,
    create_device_pairing_code,
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
    register_local_device,
    register_remote_device,
    revoke_device,
    rotate_device_token,
    save_screen_activity,
    set_active_device,
    start_work_session,
    update_device_heartbeat,
    upsert_app_usage,
)
