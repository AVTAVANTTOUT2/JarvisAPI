package fr.jarvis.companion.core.location

import fr.jarvis.companion.core.database.PendingLocationEntity
import kotlin.math.abs

data class LocationFingerprint(
    val latitude: Double,
    val longitude: Double,
    val accuracy: Float,
    val capturedAt: Long,
)

class SyncFingerprintCache(
    private val capacity: Int = LocationConstants.SYNC_FINGERPRINT_RING_SIZE,
) {
    private val ring = ArrayDeque<LocationFingerprint>()

    fun add(fingerprint: LocationFingerprint) {
        ring.addLast(fingerprint)
        while (ring.size > capacity) {
            ring.removeFirst()
        }
    }

    fun recent(): List<LocationFingerprint> = ring.toList()
}

class LocationDeduplicator(
    private val fingerprintCache: SyncFingerprintCache,
) {
    fun shouldKeep(
        candidate: CapturedLocation,
        recent: List<PendingLocationEntity>,
    ): Boolean {
        val comparisons = buildComparisons(candidate, recent)
        if (comparisons.isEmpty()) return true

        for (other in comparisons) {
            val distance = haversineMeters(
                candidate.latitude,
                candidate.longitude,
                other.latitude,
                other.longitude,
            )
            val deltaT = abs(candidate.capturedAt - other.capturedAt)

            val accuracyBetter = candidate.accuracy <= other.accuracy * 0.7f
            if (distance >= 25.0 || deltaT >= 60_000L || accuracyBetter) {
                continue
            }

            val similarAccuracy = abs(candidate.accuracy - other.accuracy) <= other.accuracy * 0.15f
            if (distance < 15.0 && deltaT < 45_000L && similarAccuracy) {
                return false
            }
        }
        return true
    }

    fun recordSynced(fingerprint: LocationFingerprint) {
        fingerprintCache.add(fingerprint)
    }

    private fun buildComparisons(
        candidate: CapturedLocation,
        recent: List<PendingLocationEntity>,
    ): List<LocationFingerprint> {
        val fromDb = recent
            .take(LocationConstants.DEDUP_COMPARE_LIMIT)
            .map { entity ->
                LocationFingerprint(
                    latitude = entity.latitude,
                    longitude = entity.longitude,
                    accuracy = entity.accuracy,
                    capturedAt = entity.capturedAt,
                )
            }
        val fromCache = fingerprintCache.recent()
        return (fromDb + fromCache).distinctBy { "${it.latitude}:${it.longitude}:${it.capturedAt}" }
    }

    private fun haversineMeters(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val r = 6_371_000.0
        val dLat = Math.toRadians(lat2 - lat1)
        val dLon = Math.toRadians(lon2 - lon1)
        val a = kotlin.math.sin(dLat / 2) * kotlin.math.sin(dLat / 2) +
            kotlin.math.cos(Math.toRadians(lat1)) * kotlin.math.cos(Math.toRadians(lat2)) *
            kotlin.math.sin(dLon / 2) * kotlin.math.sin(dLon / 2)
        val c = 2 * kotlin.math.atan2(kotlin.math.sqrt(a), kotlin.math.sqrt(1 - a))
        return r * c
    }
}
