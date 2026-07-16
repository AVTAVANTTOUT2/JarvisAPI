package fr.jarvis.companion.core.database

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName = "cached_briefings")
data class CachedBriefingEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val kind: String,
    val content: String,
    val fetchedAtMillis: Long,
    val validForDate: String,
)

@Entity(tableName = "cached_tasks")
data class CachedTaskEntity(
    @PrimaryKey val serverId: Long,
    val title: String,
    val description: String,
    val priority: String,
    val status: String,
    val dueDate: String?,
    val category: String?,
    val updatedAtMillis: Long,
)

@Entity(tableName = "cached_events")
data class CachedEventEntity(
    @PrimaryKey(autoGenerate = true) val localId: Long = 0,
    val serverId: String,
    val title: String,
    val startIso: String,
    val endIso: String?,
    val location: String?,
    val notes: String?,
    val updatedAtMillis: Long,
)

@Entity(tableName = "cached_notifications")
data class CachedNotificationEntity(
    @PrimaryKey val serverId: Long,
    val source: String,
    val title: String,
    val content: String,
    val priority: String,
    val read: Boolean,
    val createdAt: String,
)

@Entity(tableName = "sync_metadata")
data class SyncMetadataEntity(
    @PrimaryKey val key: String,
    val lastSuccessAtMillis: Long?,
    val lastError: String?,
)

@Entity(
    tableName = "pending_locations",
    indices = [
        Index(value = ["syncState"]),
        Index(value = ["capturedAt"]),
        Index(value = ["nextRetryAt"]),
        Index(value = ["batchId"]),
        Index(value = ["clientPointId"], unique = true),
    ],
)
data class PendingLocationEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val clientPointId: String,
    val latitude: Double,
    val longitude: Double,
    val altitude: Double?,
    val accuracy: Float,
    val speed: Float?,
    val bearing: Float?,
    val provider: String?,
    val capturedAt: Long,
    val createdAt: Long,
    val syncState: String,
    val batchId: String? = null,
    val retryCount: Int = 0,
    val nextRetryAt: Long? = null,
    val lastAttemptAt: Long? = null,
    val lastErrorCode: String? = null,
    val lastErrorMessage: String? = null,
)

object PendingLocationSyncState {
    const val PENDING = "PENDING"
    const val SENDING = "SENDING"
    const val SYNCED = "SYNCED"
    const val FAILED_RETRYABLE = "FAILED_RETRYABLE"
    const val FAILED_PERMANENT = "FAILED_PERMANENT"
    const val CANCELLED = "CANCELLED"
    const val INVALID = "INVALID"
}

@Entity(tableName = "location_sync_lock")
data class LocationSyncLockEntity(
    @PrimaryKey val id: Int = 1,
    val lockedBy: String? = null,
    val lockedAt: Long? = null,
    val expiresAt: Long? = null,
)

object LocationSyncMetadataKeys {
    const val LAST_SYNC_AT = "location.last_sync_at"
    const val LAST_BATCH_SIZE = "location.last_batch_size"
    const val LAST_HTTP_STATUS = "location.last_http_status"
    const val LAST_TIMELINE_JSON = "location.last_timeline_json"
}
