package fr.jarvis.companion.core.database

import androidx.room.Entity
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

@Entity(tableName = "pending_locations")
data class PendingLocationEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val latitude: Double,
    val longitude: Double,
    val altitude: Double,
    val accuracy: Float,
    val speed: Float,
    val bearing: Float,
    val provider: String?,
    val capturedAtMillis: Long,
    val createdAtMillis: Long,
    val retryCount: Int,
    val lastError: String?,
    val syncState: String,
)

object PendingLocationSyncState {
    const val PENDING = "pending"
    const val SYNCED = "synced"
    const val FAILED = "failed"
}
