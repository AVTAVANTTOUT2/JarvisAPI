package fr.jarvis.companion.core.location

object LocationConstants {
    const val MAX_BATCH_SIZE = 50
    const val LOCK_TTL_MS = 5 * 60 * 1000L
    const val SENDING_RECLAIM_MS = 10 * 60 * 1000L
    const val RETRY_BASE_MS = 30_000L
    const val RETRY_MAX_MS = 3_600_000L
    const val DEDUP_COMPARE_LIMIT = 5
    const val SYNC_FINGERPRINT_RING_SIZE = 5
    const val MAX_PENDING_AGE_DAYS = 30
    const val MAX_PENDING_COUNT = 20_000
    const val FAILED_PERMANENT_RETENTION_DAYS = 7
    const val INVALID_RETENTION_DAYS = 3

    const val MIN_DISTANCE_MOVING_METERS = 50f
    const val MIN_DISTANCE_STATIONARY_METERS = 100f
    const val MIN_DISTANCE_LOW_BATTERY_METERS = 150f
    const val MIN_TIME_MOVING_MS = 5 * 60 * 1000L
    const val MIN_TIME_STATIONARY_MS = 12 * 60 * 1000L
    const val MIN_TIME_LOW_BATTERY_MS = 15 * 60 * 1000L
    const val LOW_BATTERY_PERCENT = 20

    const val META_LAST_SYNC_AT = "location.last_sync_at"
    const val META_LAST_BATCH_SIZE = "location.last_batch_size"
    const val META_LAST_HTTP_STATUS = "location.last_http_status"
    const val META_LAST_TIMELINE_JSON = "location.last_timeline_json"
}
