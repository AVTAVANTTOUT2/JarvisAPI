package fr.jarvis.companion.core.database

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface CachedBriefingDao {
    @Query("SELECT * FROM cached_briefings ORDER BY fetchedAtMillis DESC LIMIT 1")
    fun observeLatest(): Flow<CachedBriefingEntity?>

    @Query("SELECT * FROM cached_briefings WHERE kind = :kind ORDER BY fetchedAtMillis DESC LIMIT 1")
    fun observeByKind(kind: String): Flow<CachedBriefingEntity?>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: CachedBriefingEntity)

    @Query("DELETE FROM cached_briefings WHERE kind = :kind AND validForDate != :validForDate")
    suspend fun deleteStaleForKind(kind: String, validForDate: String)
}

@Dao
interface CachedTaskDao {
    @Query("SELECT * FROM cached_tasks ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, updatedAtMillis DESC")
    fun observeActive(): Flow<List<CachedTaskEntity>>

    @Query("SELECT * FROM cached_tasks WHERE status != 'done' ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END")
    fun observeOpen(): Flow<List<CachedTaskEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(entities: List<CachedTaskEntity>)

    @Query("DELETE FROM cached_tasks WHERE serverId NOT IN (:serverIds)")
    suspend fun deleteNotIn(serverIds: List<Long>)
}

@Dao
interface CachedEventDao {
    @Query("SELECT * FROM cached_events ORDER BY startIso ASC")
    fun observeUpcoming(): Flow<List<CachedEventEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(entities: List<CachedEventEntity>)

    @Query("DELETE FROM cached_events")
    suspend fun deleteAll()
}

@Dao
interface CachedNotificationDao {
    @Query("SELECT * FROM cached_notifications WHERE read = 0 ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, createdAt DESC")
    fun observeUnread(): Flow<List<CachedNotificationEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(entities: List<CachedNotificationEntity>)

    @Query("DELETE FROM cached_notifications WHERE serverId NOT IN (:serverIds)")
    suspend fun deleteNotIn(serverIds: List<Long>)
}

@Dao
interface SyncMetadataDao {
    @Query("SELECT * FROM sync_metadata WHERE `key` = :key")
    fun observe(key: String): Flow<SyncMetadataEntity?>

    @Query("SELECT * FROM sync_metadata")
    fun observeAll(): Flow<List<SyncMetadataEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: SyncMetadataEntity)
}

@Dao
interface PendingLocationDao {
    @Query("SELECT COUNT(*) FROM pending_locations WHERE syncState = :state")
    fun observeCountByState(state: String): Flow<Int>

    @Query("SELECT * FROM pending_locations WHERE syncState = :state ORDER BY capturedAtMillis ASC")
    fun observeByState(state: String): Flow<List<PendingLocationEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entity: PendingLocationEntity): Long

    @Query("UPDATE pending_locations SET syncState = :state, lastError = :error, retryCount = retryCount + 1 WHERE id = :id")
    suspend fun markFailed(id: Long, state: String, error: String?)

    @Query("DELETE FROM pending_locations WHERE id = :id")
    suspend fun delete(id: Long)
}
