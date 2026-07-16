package fr.jarvis.companion.core.location

import android.content.Context
import fr.jarvis.companion.core.database.PendingLocationEntity
import fr.jarvis.companion.core.database.SyncMetadataEntity
import fr.jarvis.companion.core.sync.LocationSyncWorker
import fr.jarvis.companion.data.JarvisRepository
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.LocationBatchPoint
import fr.jarvis.companion.network.LocationBatchRequest
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class LocationSyncOutcome(
    val syncedCount: Int = 0,
    val skippedNoToken: Boolean = false,
    val lockNotAcquired: Boolean = false,
    val unauthorized: Boolean = false,
    val error: String? = null,
)

class LocationSyncCoordinator(
    private val context: Context,
    private val store: PendingLocationStore,
    private val repository: JarvisRepository,
    private val deduplicator: LocationDeduplicator,
    private val syncMetadataDao: fr.jarvis.companion.core.database.SyncMetadataDao,
) {
    fun requestSync(context: Context) {
        requestImmediateSync(context)
    }

    fun requestImmediateSync(context: Context) {
        LocationRuntimeDiagnostics.onSyncRequested()
        LocationSyncWorker.enqueueNow(context)
    }

    suspend fun recordCaptured() {
        appendTimeline("Capturé", System.currentTimeMillis())
    }

    suspend fun syncOnce(workerId: String = UUID.randomUUID().toString()): LocationSyncOutcome {
        if (JarvisSettings.nativeToken(context).isBlank()) {
            return LocationSyncOutcome(skippedNoToken = true)
        }

        store.ensureLockRow()
        store.purgeRetention()

        val now = System.currentTimeMillis()
        store.reclaimStaleSending(now)

        if (!store.tryAcquireLock(workerId, now)) {
            return LocationSyncOutcome(lockNotAcquired = true)
        }

        return try {
            doSync(workerId, now)
        } finally {
            store.releaseLock(workerId)
        }
    }

    private suspend fun doSync(workerId: String, now: Long): LocationSyncOutcome {
        val batchId = UUID.randomUUID().toString()
        val batch = store.reserveBatch(batchId, LocationConstants.MAX_BATCH_SIZE, now)
        if (batch.isEmpty()) {
            return LocationSyncOutcome()
        }

        LocationRuntimeDiagnostics.onBatchReserved(batch.size)
        appendTimeline("Batch créé", now)

        val request = LocationBatchRequest(
            points = batch.map { it.toBatchPoint() },
        )

        appendTimeline("Envoyé", System.currentTimeMillis())

        val result = repository.postLocationBatch(request)
        updateHttpMetadata(result.status)

        if (result.unauthorized) {
            appendTimeline("Échec auth", System.currentTimeMillis())
            store.markBatchFailedRetryable(
                ids = batch.map { it.id },
                errorCode = "AUTH",
                errorMessage = result.error,
                retryCount = batch.firstOrNull()?.retryCount?.plus(1) ?: 1,
            )
            store.clearBatch(batchId)
            LocationRuntimeDiagnostics.onBatchResponse(0, 0, batch.size, result.status)
            return LocationSyncOutcome(unauthorized = true, error = result.error)
        }

        if (!result.ok) {
            val errorCode = when (result.status) {
                429 -> "HTTP_429"
                in 500..599 -> "HTTP_5xx"
                else -> "NETWORK"
            }
            appendTimeline("Échec réseau", System.currentTimeMillis())
            val retryCount = (batch.firstOrNull()?.retryCount ?: 0) + 1
            store.markBatchFailedRetryable(
                ids = batch.map { it.id },
                errorCode = errorCode,
                errorMessage = result.error,
                retryCount = retryCount,
            )
            store.clearBatch(batchId)
            LocationRuntimeDiagnostics.onBatchResponse(0, 0, batch.size, result.status)
            return LocationSyncOutcome(error = result.error)
        }

        val acceptedIds = result.accepted.toSet()
        val duplicateIds = result.duplicates.toSet()
        val rejectedMap = result.rejected.associateBy { it.client_point_id }

        val toDelete = mutableListOf<Long>()
        val toReject = mutableListOf<String>()

        for (point in batch) {
            when {
                point.clientPointId in acceptedIds || point.clientPointId in duplicateIds -> {
                    toDelete.add(point.id)
                    deduplicator.recordSynced(
                        LocationFingerprint(
                            latitude = point.latitude,
                            longitude = point.longitude,
                            accuracy = point.accuracy,
                            capturedAt = point.capturedAt,
                        ),
                    )
                }
                rejectedMap.containsKey(point.clientPointId) -> {
                    toReject.add(point.clientPointId)
                }
                else -> {
                    store.markBatchFailedRetryable(
                        ids = listOf(point.id),
                        errorCode = "NETWORK",
                        errorMessage = "Réponse partielle sans statut",
                        retryCount = point.retryCount + 1,
                    )
                }
            }
        }

        if (toReject.isNotEmpty()) {
            for (clientId in toReject) {
                val reason = rejectedMap[clientId]?.reason
                store.markRejected(listOf(clientId), reason)
            }
        }

        store.deleteSynced(toDelete)
        store.clearBatch(batchId)

        val syncedCount = toDelete.size
        LocationRuntimeDiagnostics.onBatchResponse(
            accepted = result.accepted.size,
            duplicates = result.duplicates.size,
            rejected = toReject.size,
            httpStatus = result.status,
        )

        if (syncedCount > 0) {
            val syncAt = System.currentTimeMillis()
            syncMetadataDao.upsert(
                SyncMetadataEntity(
                    key = LocationConstants.META_LAST_SYNC_AT,
                    lastSuccessAtMillis = syncAt,
                    lastError = null,
                ),
            )
            syncMetadataDao.upsert(
                SyncMetadataEntity(
                    key = LocationConstants.META_LAST_BATCH_SIZE,
                    lastSuccessAtMillis = syncedCount.toLong(),
                    lastError = null,
                ),
            )
            appendTimeline("Confirmé", syncAt)
        }

        return LocationSyncOutcome(syncedCount = syncedCount)
    }

    private suspend fun updateHttpMetadata(status: Int) {
        syncMetadataDao.upsert(
            SyncMetadataEntity(
                key = LocationConstants.META_LAST_HTTP_STATUS,
                lastSuccessAtMillis = status.toLong(),
                lastError = null,
            ),
        )
    }

    private suspend fun appendTimeline(label: String, at: Long) {
        val key = LocationConstants.META_LAST_TIMELINE_JSON
        val currentJson = readTimelineJson()
        val array = try {
            JSONArray(currentJson ?: "[]")
        } catch (_: Exception) {
            JSONArray()
        }
        val entry = JSONObject()
            .put("at", at)
            .put("label", label)
        array.put(entry)
        while (array.length() > 20) {
            array.remove(0)
        }
        syncMetadataDao.upsert(
            SyncMetadataEntity(
                key = key,
                lastSuccessAtMillis = at,
                lastError = array.toString(),
            ),
        )
    }

    private suspend fun readTimelineJson(): String? =
        syncMetadataDao.get(LocationConstants.META_LAST_TIMELINE_JSON)?.lastError

    private fun PendingLocationEntity.toBatchPoint(): LocationBatchPoint = LocationBatchPoint(
        client_point_id = clientPointId,
        latitude = latitude,
        longitude = longitude,
        altitude = altitude,
        accuracy = accuracy,
        speed = speed,
        bearing = bearing,
        provider = provider,
        captured_at = capturedAt,
        source = "android_background",
    )
}
