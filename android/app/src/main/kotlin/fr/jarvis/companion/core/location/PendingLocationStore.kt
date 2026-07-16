package fr.jarvis.companion.core.location

import fr.jarvis.companion.core.database.JarvisDatabase
import fr.jarvis.companion.core.database.PendingLocationEntity
import fr.jarvis.companion.core.database.PendingLocationSyncState
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import java.util.concurrent.TimeUnit

data class EnqueueResult(
    val rowId: Long,
    val clientPointId: String,
)

class PendingLocationStore(
    private val database: JarvisDatabase,
) {
    private val dao get() = database.pendingLocationDao()
    private val lockDao get() = database.locationSyncLockDao()

    suspend fun ensureLockRow() {
        lockDao.ensureRow()
    }

    suspend fun enqueue(
        location: CapturedLocation,
        syncState: String = PendingLocationSyncState.PENDING,
        errorCode: String? = null,
        errorMessage: String? = null,
    ): EnqueueResult {
        val now = System.currentTimeMillis()
        val clientPointId = UUID.randomUUID().toString()
        val entity = PendingLocationEntity(
            clientPointId = clientPointId,
            latitude = location.latitude,
            longitude = location.longitude,
            altitude = location.altitude,
            accuracy = location.accuracy,
            speed = location.speed,
            bearing = location.bearing,
            provider = location.provider,
            capturedAt = location.capturedAt,
            createdAt = now,
            syncState = syncState,
            lastErrorCode = errorCode,
            lastErrorMessage = errorMessage,
        )
        val rowId = dao.insert(entity)
        return EnqueueResult(rowId = rowId, clientPointId = clientPointId)
    }

    suspend fun getRecentForDedup(limit: Int = LocationConstants.DEDUP_COMPARE_LIMIT): List<PendingLocationEntity> =
        dao.getRecentForDedup(limit)

    fun observeCountByState(state: String): Flow<Int> = dao.observeCountByState(state)

    suspend fun countByState(state: String): Int = dao.countByState(state)

    suspend fun reclaimStaleSending(now: Long = System.currentTimeMillis()) {
        val cutoff = now - LocationConstants.SENDING_RECLAIM_MS
        dao.reclaimStaleSending(cutoff)
    }

    suspend fun tryAcquireLock(workerId: String, now: Long = System.currentTimeMillis()): Boolean {
        lockDao.clearExpired(now)
        val expiresAt = now + LocationConstants.LOCK_TTL_MS
        return lockDao.tryAcquire(workerId, now, expiresAt, now) > 0
    }

    suspend fun releaseLock(workerId: String) {
        lockDao.release(workerId)
    }

    suspend fun reserveBatch(
        batchId: String,
        limit: Int = LocationConstants.MAX_BATCH_SIZE,
        now: Long = System.currentTimeMillis(),
    ): List<PendingLocationEntity> {
        val eligible = dao.getEligibleForSync(limit = limit, now = now)
        if (eligible.isEmpty()) return emptyList()
        val ids = eligible.map { it.id }
        dao.reserveBatch(ids, batchId, PendingLocationSyncState.SENDING, now)
        return eligible.map {
            it.copy(syncState = PendingLocationSyncState.SENDING, batchId = batchId, lastAttemptAt = now)
        }
    }

    suspend fun deleteSynced(ids: List<Long>) {
        if (ids.isNotEmpty()) dao.deleteByIds(ids)
    }

    suspend fun markBatchFailedRetryable(
        ids: List<Long>,
        errorCode: String?,
        errorMessage: String?,
        retryCount: Int,
    ) {
        if (ids.isEmpty()) return
        val nextRetry = computeNextRetryAt(retryCount)
        dao.markFailedRetryable(ids, nextRetryAt = nextRetry, errorCode = errorCode, errorMessage = errorMessage)
    }

    suspend fun markRejected(clientPointIds: List<String>, reason: String?) {
        if (clientPointIds.isEmpty()) return
        dao.markFailedPermanent(
            clientPointIds = clientPointIds,
            errorCode = "REJECTED",
            errorMessage = reason,
        )
    }

    suspend fun clearBatch(batchId: String) {
        dao.clearBatchId(batchId)
    }

    suspend fun getOldestPendingCapturedAt(): Long? = dao.getOldestPendingCapturedAt()

    suspend fun cancelAllPending() {
        dao.deleteByState(PendingLocationSyncState.PENDING)
        dao.deleteByState(PendingLocationSyncState.FAILED_RETRYABLE)
    }

    suspend fun clearInvalid() {
        dao.deleteByState(PendingLocationSyncState.INVALID)
    }

    suspend fun purgeRetention(now: Long = System.currentTimeMillis()) {
        val pendingCutoff = now - TimeUnit.DAYS.toMillis(LocationConstants.MAX_PENDING_AGE_DAYS.toLong())
        dao.deleteByStatesOlderThan(
            listOf(PendingLocationSyncState.PENDING, PendingLocationSyncState.FAILED_RETRYABLE),
            pendingCutoff,
        )
        val failedCutoff = now - TimeUnit.DAYS.toMillis(LocationConstants.FAILED_PERMANENT_RETENTION_DAYS.toLong())
        dao.deleteByStatesOlderThan(listOf(PendingLocationSyncState.FAILED_PERMANENT), failedCutoff)
        val invalidCutoff = now - TimeUnit.DAYS.toMillis(LocationConstants.INVALID_RETENTION_DAYS.toLong())
        dao.deleteByStatesOlderThan(listOf(PendingLocationSyncState.INVALID), invalidCutoff)
        val total = dao.countAll()
        if (total > LocationConstants.MAX_PENDING_COUNT) {
            val excess = total - LocationConstants.MAX_PENDING_COUNT
            val oldest = dao.getEligibleForSync(limit = excess, now = now)
            if (oldest.isNotEmpty()) {
                dao.deleteByIds(oldest.map { it.id })
            }
        }
    }

    companion object {
        fun computeNextRetryAt(retryCount: Int, now: Long = System.currentTimeMillis()): Long {
            val exponent = retryCount.coerceAtMost(10)
            val delay = (LocationConstants.RETRY_BASE_MS * (1L shl exponent))
                .coerceAtMost(LocationConstants.RETRY_MAX_MS)
            return now + delay
        }
    }
}
