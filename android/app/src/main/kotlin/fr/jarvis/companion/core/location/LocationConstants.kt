package fr.jarvis.companion.core.location

object LocationConstants {
    const val MAX_BATCH_SIZE = 50
    /** TTL du verrou sync : assez long pour un batch HTTP, assez court après force-stop/reboot. */
    const val LOCK_TTL_MS = 90_000L
    /**
     * Filet de sécurité si un lot `SENDING` survit hors verrou.
     * Avec lock exclusif, [PendingLocationStore.reclaimOrphanedSending] reprend tout de suite.
     */
    const val SENDING_RECLAIM_MS = 90_000L
    const val RETRY_BASE_MS = 15_000L
    const val RETRY_MAX_MS = 3_600_000L
    const val DEDUP_COMPARE_LIMIT = 5
    const val SYNC_FINGERPRINT_RING_SIZE = 5
    const val MAX_PENDING_AGE_DAYS = 30
    const val MAX_PENDING_COUNT = 20_000
    const val FAILED_PERMANENT_RETENTION_DAYS = 7
    const val INVALID_RETENTION_DAYS = 3

    const val LIVE_MIN_TIME_MS = 60_000L
    const val LIVE_MIN_DISTANCE_METERS = 0f
    const val BALANCED_MIN_TIME_MS = 5 * 60_000L
    const val BALANCED_MIN_DISTANCE_METERS = 25f
    const val ECONOMY_MIN_TIME_MS = 15 * 60_000L
    const val ECONOMY_MIN_DISTANCE_METERS = 50f
    const val HEARTBEAT_INTERVAL_MS = 60_000L
    const val MAX_LAST_KNOWN_AGE_MS = 5 * 60_000L
    const val LIVE_MAX_ACCURACY_METERS = 250f
    const val BALANCED_MAX_ACCURACY_METERS = 150f
    const val ECONOMY_MAX_ACCURACY_METERS = 200f
    const val LOW_BATTERY_PERCENT = 20

    const val META_LAST_SYNC_AT = "location.last_sync_at"
    const val META_LAST_BATCH_SIZE = "location.last_batch_size"
    const val META_LAST_HTTP_STATUS = "location.last_http_status"
    const val META_LAST_TIMELINE_JSON = "location.last_timeline_json"
    const val META_LAST_CAPTURE_AT = "location.last_capture_at"
    const val META_LAST_CALLBACK_AT = "location.last_callback_at"
    const val META_LAST_REJECT_REASON = "location.last_reject_reason"
    const val META_LAST_INSERT_AT = "location.last_insert_at"

    const val MIN_DISTANCE_MOVING_METERS = BALANCED_MIN_DISTANCE_METERS
    const val MIN_DISTANCE_STATIONARY_METERS = 100f
    const val MIN_DISTANCE_LOW_BATTERY_METERS = ECONOMY_MIN_DISTANCE_METERS
    const val MIN_TIME_MOVING_MS = BALANCED_MIN_TIME_MS
    const val MIN_TIME_STATIONARY_MS = 12 * 60_000L
    const val MIN_TIME_LOW_BATTERY_MS = ECONOMY_MIN_TIME_MS
}
