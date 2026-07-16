package fr.jarvis.companion.core.database

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
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

    @Query("SELECT * FROM sync_metadata WHERE `key` = :key LIMIT 1")
    suspend fun get(key: String): SyncMetadataEntity?

    @Query("SELECT * FROM sync_metadata")
    fun observeAll(): Flow<List<SyncMetadataEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: SyncMetadataEntity)
}

@Dao
interface PendingLocationDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(entity: PendingLocationEntity): Long

    @Query("DELETE FROM pending_locations WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("DELETE FROM pending_locations WHERE id IN (:ids)")
    suspend fun deleteByIds(ids: List<Long>)

    @Query("DELETE FROM pending_locations WHERE syncState = :state")
    suspend fun deleteByState(state: String)

    @Query("DELETE FROM pending_locations WHERE syncState IN (:states) AND createdAt < :olderThan")
    suspend fun deleteByStatesOlderThan(states: List<String>, olderThan: Long)

    @Query("DELETE FROM pending_locations WHERE syncState = :syncedState")
    suspend fun deleteSynced(syncedState: String = PendingLocationSyncState.SYNCED)

    @Query("SELECT COUNT(*) FROM pending_locations WHERE syncState = :state")
    fun observeCountByState(state: String): Flow<Int>

    @Query("SELECT COUNT(*) FROM pending_locations WHERE syncState = :state")
    suspend fun countByState(state: String): Int

    @Query("SELECT * FROM pending_locations WHERE syncState = :state ORDER BY capturedAt ASC")
    fun observeByState(state: String): Flow<List<PendingLocationEntity>>

    @Query(
        """
        SELECT * FROM pending_locations
        WHERE syncState IN (:pendingState, :retryableState)
        AND (nextRetryAt IS NULL OR nextRetryAt <= :now)
        ORDER BY capturedAt ASC
        LIMIT :limit
        """,
    )
    suspend fun getEligibleForSync(
        pendingState: String = PendingLocationSyncState.PENDING,
        retryableState: String = PendingLocationSyncState.FAILED_RETRYABLE,
        limit: Int,
        now: Long,
    ): List<PendingLocationEntity>

    @Query(
        """
        UPDATE pending_locations
        SET syncState = :state, batchId = :batchId, lastAttemptAt = :now
        WHERE id IN (:ids)
        """,
    )
    suspend fun reserveBatch(ids: List<Long>, batchId: String, state: String, now: Long)

    @Query(
        """
        UPDATE pending_locations
        SET syncState = :pendingState, batchId = NULL, lastAttemptAt = NULL
        WHERE syncState = :sendingState AND lastAttemptAt IS NOT NULL AND lastAttemptAt < :cutoff
        """,
    )
    suspend fun reclaimStaleSending(
        cutoff: Long,
        pendingState: String = PendingLocationSyncState.PENDING,
        sendingState: String = PendingLocationSyncState.SENDING,
    )

    @Query(
        """
        UPDATE pending_locations
        SET syncState = :pendingState, batchId = NULL, lastAttemptAt = NULL
        WHERE syncState = :sendingState
        """,
    )
    suspend fun reclaimAllSending(
        pendingState: String = PendingLocationSyncState.PENDING,
        sendingState: String = PendingLocationSyncState.SENDING,
    )

    @Query("DELETE FROM pending_locations WHERE id IN (:ids)")
    suspend fun applySyncedDelete(ids: List<Long>)

    @Query(
        """
        UPDATE pending_locations
        SET syncState = :state, batchId = NULL, retryCount = retryCount + 1,
            nextRetryAt = :nextRetryAt, lastErrorCode = :errorCode, lastErrorMessage = :errorMessage
        WHERE id IN (:ids)
        """,
    )
    suspend fun markFailedRetryable(
        ids: List<Long>,
        state: String = PendingLocationSyncState.FAILED_RETRYABLE,
        nextRetryAt: Long,
        errorCode: String?,
        errorMessage: String?,
    )

    @Query(
        """
        UPDATE pending_locations
        SET syncState = :state, batchId = NULL, lastErrorCode = :errorCode, lastErrorMessage = :errorMessage
        WHERE clientPointId IN (:clientPointIds)
        """,
    )
    suspend fun markFailedPermanent(
        clientPointIds: List<String>,
        state: String = PendingLocationSyncState.FAILED_PERMANENT,
        errorCode: String? = "REJECTED",
        errorMessage: String? = null,
    )

    @Query(
        """
        UPDATE pending_locations
        SET syncState = :state, batchId = NULL, lastErrorCode = :errorCode, lastErrorMessage = :errorMessage
        WHERE clientPointId IN (:clientPointIds)
        """,
    )
    suspend fun markInvalid(
        clientPointIds: List<String>,
        state: String = PendingLocationSyncState.INVALID,
        errorCode: String? = "INVALID",
        errorMessage: String? = null,
    )

    @Query(
        """
        SELECT MIN(capturedAt) FROM pending_locations
        WHERE syncState IN (:pendingState, :retryableState)
        """,
    )
    suspend fun getOldestPendingCapturedAt(
        pendingState: String = PendingLocationSyncState.PENDING,
        retryableState: String = PendingLocationSyncState.FAILED_RETRYABLE,
    ): Long?

    @Query("UPDATE pending_locations SET batchId = NULL WHERE batchId = :batchId")
    suspend fun clearBatchId(batchId: String)

    @Query("SELECT COUNT(*) FROM pending_locations")
    suspend fun countAll(): Int

    @Query(
        """
        SELECT * FROM pending_locations
        WHERE syncState NOT IN (:invalidState, :cancelledState)
        ORDER BY capturedAt DESC
        LIMIT :limit
        """,
    )
    suspend fun getRecentForDedup(
        limit: Int,
        invalidState: String = PendingLocationSyncState.INVALID,
        cancelledState: String = PendingLocationSyncState.CANCELLED,
    ): List<PendingLocationEntity>
}

@Dao
interface LocationSyncLockDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun ensureRow(entity: LocationSyncLockEntity = LocationSyncLockEntity())

    @Query(
        """
        UPDATE location_sync_lock
        SET lockedBy = :lockedBy, lockedAt = :lockedAt, expiresAt = :expiresAt
        WHERE id = 1 AND (lockedBy IS NULL OR expiresAt IS NULL OR expiresAt < :now)
        """,
    )
    suspend fun tryAcquire(lockedBy: String, lockedAt: Long, expiresAt: Long, now: Long): Int

    @Query(
        """
        UPDATE location_sync_lock
        SET lockedBy = NULL, lockedAt = NULL, expiresAt = NULL
        WHERE id = 1 AND lockedBy = :lockedBy
        """,
    )
    suspend fun release(lockedBy: String)

    @Query(
        """
        UPDATE location_sync_lock
        SET lockedBy = NULL, lockedAt = NULL, expiresAt = NULL
        WHERE id = 1 AND expiresAt IS NOT NULL AND expiresAt < :now
        """,
    )
    suspend fun clearExpired(now: Long)

    @Query("SELECT * FROM location_sync_lock WHERE id = 1")
    suspend fun getLock(): LocationSyncLockEntity?
}
