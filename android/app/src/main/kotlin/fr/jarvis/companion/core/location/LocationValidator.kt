package fr.jarvis.companion.core.location

data class LocationValidationConfig(
    val maxAccuracyMeters: Float = 100f,
    val maxAgeMs: Long = 3 * 60 * 1000L,
    val maxFutureSkewMs: Long = 60_000L,
    val maxSpeedMps: Float = 90f,
)

data class LocationValidationResult(
    val valid: Boolean,
    val errorCode: String? = null,
    val errorMessage: String? = null,
)

class LocationValidator(
    private val config: LocationValidationConfig = LocationValidationConfig(),
) {
    fun validate(location: CapturedLocation, now: Long = System.currentTimeMillis()): LocationValidationResult {
        if (location.latitude !in -90.0..90.0 || location.longitude !in -180.0..180.0) {
            return LocationValidationResult(false, "INVALID", "Coordonnées hors limites")
        }
        if (location.provider.isNullOrBlank()) {
            return LocationValidationResult(false, "INVALID", "Provider absent")
        }
        if (location.accuracy <= 0f || location.accuracy > config.maxAccuracyMeters) {
            return LocationValidationResult(false, "INVALID", "Précision insuffisante")
        }
        val age = now - location.capturedAt
        if (age > config.maxAgeMs) {
            return LocationValidationResult(false, "INVALID", "Point trop ancien")
        }
        if (location.capturedAt > now + config.maxFutureSkewMs) {
            return LocationValidationResult(false, "INVALID", "Horodatage futur")
        }
        val speed = location.speed
        if (speed != null && speed > config.maxSpeedMps) {
            return LocationValidationResult(false, "INVALID", "Vitesse incohérente")
        }
        return LocationValidationResult(true)
    }

    companion object {
        fun economyConfig(): LocationValidationConfig =
            LocationValidationConfig(maxAccuracyMeters = 150f)
    }
}
