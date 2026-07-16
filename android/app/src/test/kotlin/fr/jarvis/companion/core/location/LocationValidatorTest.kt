package fr.jarvis.companion.core.location

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocationValidatorTest {
    private val validator = LocationValidator()
    private val now = 1_000_000L

    @Test
    fun acceptsValidPoint() {
        val result = validator.validate(
            CapturedLocation(
                latitude = 50.63,
                longitude = 3.06,
                altitude = 20.0,
                accuracy = 12f,
                speed = 1.2f,
                bearing = 90f,
                provider = "gps",
                capturedAt = now - 30_000L,
            ),
            now = now,
        )
        assertTrue(result.valid)
    }

    @Test
    fun rejectsOutOfRangeLatitude() {
        val result = validator.validate(
            CapturedLocation(
                latitude = 95.0,
                longitude = 3.0,
                altitude = null,
                accuracy = 10f,
                speed = null,
                bearing = null,
                provider = "gps",
                capturedAt = now,
            ),
            now = now,
        )
        assertFalse(result.valid)
        assertTrue(result.errorCode == "INVALID")
    }

    @Test
    fun rejectsPoorAccuracy() {
        val result = validator.validate(
            CapturedLocation(
                latitude = 50.0,
                longitude = 3.0,
                altitude = null,
                accuracy = 200f,
                speed = null,
                bearing = null,
                provider = "gps",
                capturedAt = now,
            ),
            now = now,
        )
        assertFalse(result.valid)
    }

    @Test
    fun rejectsStalePoint() {
        val result = validator.validate(
            CapturedLocation(
                latitude = 50.0,
                longitude = 3.0,
                altitude = null,
                accuracy = 10f,
                speed = null,
                bearing = null,
                provider = "gps",
                capturedAt = now - 5 * 60_000L,
            ),
            now = now,
        )
        assertFalse(result.valid)
    }

    @Test
    fun rejectsFutureTimestamp() {
        val result = validator.validate(
            CapturedLocation(
                latitude = 50.0,
                longitude = 3.0,
                altitude = null,
                accuracy = 10f,
                speed = null,
                bearing = null,
                provider = "gps",
                capturedAt = now + 120_000L,
            ),
            now = now,
        )
        assertFalse(result.valid)
    }

    @Test
    fun economyModeAllows150m() {
        val economy = LocationValidator(LocationValidator.economyConfig())
        val result = economy.validate(
            CapturedLocation(
                latitude = 50.0,
                longitude = 3.0,
                altitude = null,
                accuracy = 140f,
                speed = null,
                bearing = null,
                provider = "gps",
                capturedAt = now,
            ),
            now = now,
        )
        assertTrue(result.valid)
    }
}
