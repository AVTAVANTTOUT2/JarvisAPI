package fr.jarvis.companion.core.location

import fr.jarvis.companion.core.database.PendingLocationEntity
import fr.jarvis.companion.core.database.PendingLocationSyncState
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocationDeduplicatorTest {
    private val cache = SyncFingerprintCache()
    private val deduplicator = LocationDeduplicator(cache)

    @Test
    fun shouldKeep_whenFarEnough() {
        val recent = listOf(
            entity(lat = 50.0, lng = 3.0, accuracy = 20f, capturedAt = 1_000L),
        )
        val candidate = CapturedLocation(
            latitude = 50.001,
            longitude = 3.001,
            altitude = null,
            accuracy = 18f,
            speed = null,
            bearing = null,
            provider = "gps",
            capturedAt = 70_000L,
        )
        assertTrue(deduplicator.shouldKeep(candidate, recent))
    }

    @Test
    fun shouldReject_whenTooCloseAndTooSoon() {
        val recent = listOf(
            entity(lat = 50.0, lng = 3.0, accuracy = 20f, capturedAt = 1_000L),
        )
        val candidate = CapturedLocation(
            latitude = 50.00001,
            longitude = 3.00001,
            altitude = null,
            accuracy = 21f,
            speed = null,
            bearing = null,
            provider = "gps",
            capturedAt = 10_000L,
        )
        assertFalse(deduplicator.shouldKeep(candidate, recent))
    }

    @Test
    fun shouldKeep_heartbeatAt60s() {
        val recent = listOf(
            entity(lat = 50.0, lng = 3.0, accuracy = 20f, capturedAt = 1_000L),
        )
        val candidate = CapturedLocation(
            latitude = 50.00001,
            longitude = 3.00001,
            altitude = null,
            accuracy = 21f,
            speed = null,
            bearing = null,
            provider = "gps",
            capturedAt = 61_000L,
        )
        assertTrue(
            deduplicator.shouldKeep(
                candidate,
                recent,
                heartbeatIntervalMs = LocationConstants.HEARTBEAT_INTERVAL_MS,
            ),
        )
    }

    @Test
    fun shouldKeep_whenAccuracyMuchBetter() {
        val recent = listOf(
            entity(lat = 50.0, lng = 3.0, accuracy = 40f, capturedAt = 1_000L),
        )
        val candidate = CapturedLocation(
            latitude = 50.00001,
            longitude = 3.00001,
            altitude = null,
            accuracy = 10f,
            speed = null,
            bearing = null,
            provider = "gps",
            capturedAt = 10_000L,
        )
        assertTrue(deduplicator.shouldKeep(candidate, recent))
    }

    @Test
    fun shouldReject_againstSyncedFingerprintCache() {
        cache.add(
            LocationFingerprint(
                latitude = 50.0,
                longitude = 3.0,
                accuracy = 15f,
                capturedAt = 5_000L,
            ),
        )
        val candidate = CapturedLocation(
            latitude = 50.00001,
            longitude = 3.00001,
            altitude = null,
            accuracy = 16f,
            speed = null,
            bearing = null,
            provider = "network",
            capturedAt = 8_000L,
        )
        assertFalse(deduplicator.shouldKeep(candidate, emptyList()))
    }

    private fun entity(
        lat: Double,
        lng: Double,
        accuracy: Float,
        capturedAt: Long,
    ): PendingLocationEntity = PendingLocationEntity(
        clientPointId = "test-id",
        latitude = lat,
        longitude = lng,
        altitude = null,
        accuracy = accuracy,
        speed = null,
        bearing = null,
        provider = "gps",
        capturedAt = capturedAt,
        createdAt = capturedAt,
        syncState = PendingLocationSyncState.PENDING,
    )
}
